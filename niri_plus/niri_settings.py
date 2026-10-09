"""Safe, user-scoped Niri configuration overrides.

The generated KDL is deliberately limited to settings whose Niri 26.04 config
sections merge without replacing unrelated input-device configuration. Niri's
own validator remains the semantic authority.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import pathlib
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable


SCHEMA_VERSION = 1
SETTINGS_RELATIVE = pathlib.Path("niri-plus/settings.kdl")
SYSTEM_INCLUDE = 'include optional=true "~/.config/niri-plus/settings.kdl"'
USER_INCLUDE_START = "// niri+ managed settings include: begin"
USER_INCLUDE_END = "// niri+ managed settings include: end"

DEFAULTS: dict[str, object] = {
    "schema_version": SCHEMA_VERSION,
    "gaps": 6,
    "border_enabled": False,
    "border_width": 2,
    "center_focused_column": "never",
    "default_column_display": "normal",
    "keyboard_repeat_delay": 600,
    "keyboard_repeat_rate": 25,
    "launcher_key": "Mod+Space",
    "terminal_key": "Mod+Return",
}

CENTER_FOCUSED = {"never", "always", "on-overflow"}
COLUMN_DISPLAY = {"normal", "tabbed"}
SHORTCUT_KEYS = {
    "Mod+Space", "Mod+D", "Mod+P", "Mod+Return", "Mod+T", "Mod+Shift+Return",
    "Super+Space", "Super+D", "Super+P", "Super+Return", "Super+T", "Super+Shift+Return",
}
_INCLUDE_LINE = re.compile(r'^\s*include(?:\s+optional=true)?\s+("(?:[^"\\]|\\.)*")')
_KEY_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}\Z", re.ASCII)


class NiriSettingsError(RuntimeError):
    """A settings request could not be applied without risking user files."""


def _is_int(value: object, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _shortcut_is_safe(value: object) -> bool:
    if not isinstance(value, str) or value not in SHORTCUT_KEYS:
        return False
    parts = value.split("+")
    if len(parts) < 2 or not _KEY_TOKEN.fullmatch(parts[-1]):
        return False
    modifiers = parts[:-1]
    allowed = {"Mod", "Super", "Shift"}
    return all(part in allowed for part in modifiers) and len(set(modifiers)) == len(modifiers)


def validate_settings(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != set(DEFAULTS):
        raise NiriSettingsError("o documento precisa conter exatamente os campos de Niri+ suportados")
    if payload.get("schema_version") != SCHEMA_VERSION or type(payload.get("schema_version")) is not int:
        raise NiriSettingsError("versão das preferências Niri+ incompatível")
    for key, lower, upper in (
        ("gaps", 0, 32),
        ("border_width", 0, 8),
        ("keyboard_repeat_delay", 100, 2000),
        ("keyboard_repeat_rate", 1, 60),
    ):
        if not _is_int(payload.get(key), lower, upper):
            raise NiriSettingsError(f"valor inválido para {key}")
    if type(payload.get("border_enabled")) is not bool:
        raise NiriSettingsError("valor inválido para border_enabled")
    if payload.get("center_focused_column") not in CENTER_FOCUSED:
        raise NiriSettingsError("valor inválido para center_focused_column")
    if payload.get("default_column_display") not in COLUMN_DISPLAY:
        raise NiriSettingsError("valor inválido para default_column_display")
    for key in ("launcher_key", "terminal_key"):
        if not _shortcut_is_safe(payload.get(key)):
            raise NiriSettingsError(f"atalho não suportado para {key}")
    if payload["launcher_key"] == payload["terminal_key"]:
        raise NiriSettingsError("launcher e terminal não podem usar o mesmo atalho")
    return dict(payload)


def render_kdl(payload: object) -> str:
    settings = validate_settings(payload)
    flag = lambda value: "true" if value else "false"
    return "\n".join((
        "// Gerenciado por niri+; altere pelo Settings Center ou niri+ niri-settings.",
        "layout {",
        f"    gaps {settings['gaps']}",
        f'    center-focused-column "{settings["center_focused_column"]}"',
        f'    default-column-display "{settings["default_column_display"]}"',
        "    border {",
        f"        on {flag(settings['border_enabled'])}",
        f"        width {settings['border_width']}",
        "    }",
        "}",
        "input {",
        "    keyboard {",
        f"        repeat-delay {settings['keyboard_repeat_delay']}",
        f"        repeat-rate {settings['keyboard_repeat_rate']}",
        "    }",
        "}",
        "binds {",
        f'    {settings["launcher_key"]} {{ spawn "fuzzel"; }}',
        f'    {settings["terminal_key"]} {{ spawn "foot"; }}',
        "}",
        "",
    ))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_regular(path: pathlib.Path, *, uid: int | None, allow_root: bool = False) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise NiriSettingsError("arquivo de configuração não é regular")
    accepted = {uid} if uid is not None else set()
    if allow_root:
        accepted.add(0)
    if info.st_uid not in accepted or info.st_mode & 0o022:
        raise NiriSettingsError("arquivo de configuração tem owner ou permissões inseguros")
    return info


def _ensure_directory(path: pathlib.Path, *, uid: int, mode: int) -> None:
    path = pathlib.Path(os.path.abspath(path))
    current = pathlib.Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            try:
                current.mkdir(mode=mode)
            except FileExistsError:
                pass
            info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise NiriSettingsError("diretório de configuração contém symlink ou não é diretório")
    info = path.lstat()
    if info.st_uid != uid or info.st_mode & 0o022:
        raise NiriSettingsError("diretório de configuração tem owner ou permissões inseguros")
    os.chmod(path, mode)


def _atomic_write(path: pathlib.Path, data: bytes, *, uid: int, mode: int,
                  existing_owner: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        old = _safe_regular(path, uid=existing_owner if existing_owner is not None else uid,
                            allow_root=existing_owner is None)
        if existing_owner is None and old.st_uid not in (0, uid):
            raise NiriSettingsError("owner de arquivo gerenciado mudou")
        mode = stat.S_IMODE(old.st_mode)
    except FileNotFoundError:
        pass
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(data)
            output.flush()
            os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _user_include_block(settings_path: pathlib.Path) -> str:
    include = f"include optional=true {json.dumps(str(settings_path))}"
    return f"{USER_INCLUDE_START}\n{include}\n{USER_INCLUDE_END}"


def _add_user_include(content: str, settings_path: pathlib.Path) -> tuple[str, bool]:
    block = _user_include_block(settings_path)
    starts = content.count(USER_INCLUDE_START)
    ends = content.count(USER_INCLUDE_END)
    if starts or ends:
        if starts == ends == 1 and block in content:
            return content, False
        raise NiriSettingsError("bloco de include Niri+ foi alterado; arquivo preservado")
    include = f"include optional=true {json.dumps(str(settings_path))}"
    if content.count(include) == 1:
        return content, False
    if content.count(include) > 1:
        raise NiriSettingsError("há includes Niri+ duplicados no config do usuário")
    separator = "" if not content or content.endswith("\n") else "\n"
    return f"{content}{separator}\n{block}\n", True


def _decode_include(raw: str) -> str:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise NiriSettingsError("path de include KDL não pôde ser lido") from error
    if not isinstance(value, str) or not value or "\x00" in value:
        raise NiriSettingsError("path de include KDL inválido")
    return value


def _include_paths(text: str) -> list[str]:
    paths = []
    for line in text.splitlines():
        match = _INCLUDE_LINE.match(line)
        if match:
            paths.append(_decode_include(match.group(1)))
    return paths


class NiriSettingsManager:
    def __init__(
        self,
        *,
        home: pathlib.Path | None = None,
        config_home: pathlib.Path | None = None,
        state_home: pathlib.Path | None = None,
        system_config: pathlib.Path = pathlib.Path("/etc/niri/config.kdl"),
        environment: dict[str, str] | None = None,
        uid: int | None = None,
        validator: Callable[[pathlib.Path], tuple[bool, str]] | None = None,
        niri_binary: str | None = None,
    ) -> None:
        self.environment = dict(os.environ if environment is None else environment)
        self.uid = os.geteuid() if uid is None else uid
        self.home = pathlib.Path(home or self.environment.get("HOME") or pathlib.Path.home())
        self.config_home = pathlib.Path(config_home or self.environment.get("XDG_CONFIG_HOME") or self.home / ".config")
        self.state_home = pathlib.Path(state_home or self.environment.get("XDG_STATE_HOME") or self.home / ".local/state")
        self.system_config = pathlib.Path(system_config)
        self.settings_path = self.config_home / SETTINGS_RELATIVE
        self.state_dir = self.state_home / "niri-plus"
        self.state_path = self.state_dir / "niri-settings.json"
        self.backup_path = self.state_dir / "niri-config-before-include.kdl"
        self.lock_path = self.state_dir / "niri-settings.lock"
        self.validator = validator or self._validate_with_niri
        self.niri_binary = niri_binary

    def _active_config(self) -> tuple[pathlib.Path, str]:
        explicit = self.environment.get("NIRI_CONFIG", "")
        if explicit:
            path = pathlib.Path(explicit)
            if not path.is_file():
                raise NiriSettingsError("NIRI_CONFIG aponta para um arquivo indisponível")
            scope = "user" if path.is_relative_to(self.config_home) else "system"
            return path, scope
        user = self.config_home / "niri/config.kdl"
        if user.exists() or user.is_symlink():
            if user.is_symlink() or not user.is_file():
                raise NiriSettingsError("config Niri do usuário não é um arquivo regular")
            return user, "user"
        if self.system_config.is_file() or self.system_config.is_symlink():
            return self.system_config, "system"
        raise NiriSettingsError("config Niri ativo não encontrado")

    def _read_config(self, path: pathlib.Path, scope: str) -> tuple[bytes, os.stat_result]:
        expected_owner = self.uid if scope == "user" else 0
        info = _safe_regular(path, uid=expected_owner, allow_root=scope == "system")
        try:
            return path.read_bytes(), info
        except OSError as error:
            raise NiriSettingsError("config Niri não pôde ser lido") from error

    def _load_state(self) -> dict[str, object] | None:
        try:
            info = _safe_regular(self.state_path, uid=self.uid)
            if info.st_mode & 0o077:
                raise NiriSettingsError("state Niri+ deve ser privado")
            raw = self.state_path.read_text(encoding="utf-8")
            state = json.loads(raw)
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise NiriSettingsError("state Niri+ está ilegível; nenhum arquivo foi alterado") from error
        if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
            raise NiriSettingsError("state Niri+ incompatível; nenhum arquivo foi alterado")
        try:
            state["settings"] = validate_settings(state["settings"])
            previous = state.get("previous_settings")
            state["previous_settings"] = validate_settings(previous) if previous is not None else None
        except (KeyError, NiriSettingsError) as error:
            raise NiriSettingsError("state Niri+ inconsistente; nenhum arquivo foi alterado") from error
        return state

    def status(self) -> dict[str, object]:
        try:
            config, scope = self._active_config()
            content, _info = self._read_config(config, scope)
            text = content.decode("utf-8")
            integrated = (
                SYSTEM_INCLUDE in text if scope == "system"
                else str(self.settings_path) in text or _user_include_block(self.settings_path) in text
            )
            state = self._load_state()
            managed = False
            state_matches = None
            settings: dict[str, object] = dict(DEFAULTS)
            if state is not None:
                managed = True
                settings = state["settings"]  # type: ignore[assignment]
                try:
                    installed = self.settings_path.read_bytes()
                    state_matches = _sha256(installed) == state.get("settings_sha256")
                except OSError:
                    state_matches = False
            binary = self.niri_binary or shutil.which("niri")
            return {
                "status": "OK" if integrated and state_matches is not False else "WARNING" if integrated else "NOT_CONFIGURED",
                "configured": managed,
                "managed_file_matches": state_matches,
                "settings": settings,
                "niri_validator": "AVAILABLE" if binary else "UNAVAILABLE",
                "config_scope": scope,
                "touchpad_settings": "NOT_SUPPORTED_SAFE_MERGE",
            }
        except NiriSettingsError as error:
            return {
                "status": "WARNING",
                "configured": False,
                "managed_file_matches": None,
                "settings": dict(DEFAULTS),
                "niri_validator": "AVAILABLE" if self.niri_binary or shutil.which("niri") else "UNAVAILABLE",
                "config_scope": "unknown",
                "touchpad_settings": "NOT_SUPPORTED_SAFE_MERGE",
                "warning": str(error),
            }

    def _validate_with_niri(self, config_path: pathlib.Path) -> tuple[bool, str]:
        binary = self.niri_binary or shutil.which("niri")
        if not binary:
            return False, "niri validator is unavailable"
        try:
            result = subprocess.run(
                [binary, "validate", "--config", str(config_path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
                env={key: value for key, value in self.environment.items() if key != "NIRI_CONFIG"},
            )
        except (OSError, subprocess.SubprocessError):
            return False, "niri validator could not run"
        if result.returncode == 0:
            return True, ""
        return False, "niri validate recusou a configuração"

    def _candidate_config(
        self,
        root_path: pathlib.Path,
        root_content: str,
        override_content: str,
    ) -> tuple[tempfile.TemporaryDirectory[str], pathlib.Path]:
        _ensure_directory(self.state_dir, uid=self.uid, mode=0o700)
        temporary = tempfile.TemporaryDirectory(prefix="niri-validate-", dir=self.state_dir)
        stage_root = pathlib.Path(temporary.name) / "config"
        source_base = root_path.parent.resolve()
        candidate_override = pathlib.Path(temporary.name) / "niri-plus-settings.kdl"
        candidate_override.write_text(override_content, encoding="utf-8")
        os.chmod(candidate_override, 0o600)
        visited: set[pathlib.Path] = set()
        rewritten_root: pathlib.Path | None = None

        def stage_file(source: pathlib.Path, *, top: bool = False) -> pathlib.Path:
            nonlocal rewritten_root
            try:
                info = source.lstat()
            except OSError as error:
                raise NiriSettingsError("um include KDL necessário está indisponível") from error
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise NiriSettingsError("um include KDL usa symlink ou não é arquivo regular")
            real = source.resolve()
            try:
                relative = real.relative_to(source_base)
            except ValueError:
                raise NiriSettingsError("include relativo sai do diretório de configuração")
            if ".." in relative.parts:
                raise NiriSettingsError("include relativo inseguro")
            destination = stage_root / relative
            if real in visited:
                return destination
            visited.add(real)
            try:
                text = (root_content if top else source.read_text(encoding="utf-8"))
            except (OSError, UnicodeError) as error:
                raise NiriSettingsError("config KDL não pôde ser lido como UTF-8") from error

            changed = []
            for line in text.splitlines():
                match = _INCLUDE_LINE.match(line)
                if not match:
                    changed.append(line)
                    continue
                include_value = _decode_include(match.group(1))
                expanded = pathlib.Path(include_value.replace("~/", str(self.home) + "/"))
                try:
                    same_settings = expanded.resolve(strict=False) == self.settings_path.resolve(strict=False)
                except OSError:
                    same_settings = False
                if include_value == "~/.config/niri-plus/settings.kdl" or same_settings:
                    replacement = f"include optional=true {json.dumps(str(candidate_override))}"
                    changed.append(replacement)
                    continue
                include_path = pathlib.Path(include_value)
                if include_path.is_absolute() or include_value.startswith("~"):
                    changed.append(line)
                    continue
                if ".." in include_path.parts:
                    raise NiriSettingsError("include relativo com '..' não é aceito para validação segura")
                stage_file(source.parent / include_path)
                changed.append(line)
            if top and not any(
                candidate.startswith("include optional=true ") and str(candidate_override) in candidate
                for candidate in changed
            ):
                marker, _added = _add_user_include(text, self.settings_path)
                # The active system config already owns the optional include; if it is
                # absent, the include contract is broken and the install must be repaired.
                if "include optional=true" in text:
                    raise NiriSettingsError("include Niri+ não pôde ser preparado para validação")
                marker = marker.rstrip() + "\n" + f"include optional=true {json.dumps(str(candidate_override))}"
                changed = marker.splitlines()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("\n".join(changed) + "\n", encoding="utf-8")
            os.chmod(destination, 0o600)
            if top:
                rewritten_root = destination
            return destination

        try:
            stage_file(root_path, top=True)
            if rewritten_root is None:
                raise NiriSettingsError("config Niri não pôde ser preparado para validação")
            return temporary, rewritten_root
        except Exception:
            temporary.cleanup()
            raise

    def apply(self, payload: object) -> dict[str, object]:
        settings = validate_settings(payload)
        if self.uid == 0:
            raise NiriSettingsError("rode niri+ niri-settings como o usuário da sessão, sem sudo")
        _ensure_directory(self.config_home, uid=self.uid, mode=0o700 if self.config_home.name == "niri-plus" else 0o755)
        _ensure_directory(self.settings_path.parent, uid=self.uid, mode=0o700)
        _ensure_directory(self.state_dir, uid=self.uid, mode=0o700)
        lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            return self._apply_locked(settings)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def _apply_locked(self, settings: dict[str, object]) -> dict[str, object]:
        old_state = self._load_state()
        try:
            config_path, scope = self._active_config()
            old_config_bytes, config_info = self._read_config(config_path, scope)
            old_config_text = old_config_bytes.decode("utf-8")
        except (UnicodeError, OSError) as error:
            raise NiriSettingsError("config Niri ativo não pôde ser lido como UTF-8") from error

        if scope == "system":
            if self.config_home != self.home / ".config":
                raise NiriSettingsError("XDG_CONFIG_HOME customizado não está integrado ao include de /etc/niri")
            new_config_text = old_config_text
            include_added = False
            if SYSTEM_INCLUDE not in old_config_text:
                raise NiriSettingsError("include de preferências Niri+ ausente; instale/repare com sudo niri+ install")
        else:
            new_config_text, include_added = _add_user_include(old_config_text, self.settings_path)

        try:
            old_settings_bytes = self.settings_path.read_bytes()
            settings_info = _safe_regular(self.settings_path, uid=self.uid)
            if settings_info.st_mode & 0o077:
                raise NiriSettingsError("arquivo de preferências Niri+ deve ser privado")
            if old_state is None or _sha256(old_settings_bytes) != old_state.get("settings_sha256"):
                raise NiriSettingsError("arquivo de preferências foi alterado fora do Niri+; arquivo preservado")
        except FileNotFoundError:
            old_settings_bytes = None
        new_kdl = render_kdl(settings)
        temporary: tempfile.TemporaryDirectory[str] | None = None
        try:
            temporary, candidate = self._candidate_config(config_path, new_config_text, new_kdl)
            valid, detail = self.validator(candidate)
            if not valid:
                raise NiriSettingsError(detail or "niri validate recusou a configuração; versão anterior preservada")
        finally:
            if temporary is not None:
                temporary.cleanup()

        old_state_bytes = self.state_path.read_bytes() if self.state_path.exists() else None
        user_config_written = False
        settings_written = False
        try:
            if scope == "user" and include_added:
                if old_state is None and not self.backup_path.exists():
                    _atomic_write(self.backup_path, old_config_bytes, uid=self.uid, mode=0o600, existing_owner=self.uid)
                _atomic_write(config_path, new_config_text.encode("utf-8"), uid=self.uid,
                              mode=stat.S_IMODE(config_info.st_mode), existing_owner=self.uid)
                user_config_written = True
            _atomic_write(self.settings_path, new_kdl.encode("utf-8"), uid=self.uid, mode=0o600,
                          existing_owner=self.uid)
            settings_written = True
            next_state = {
                "schema_version": SCHEMA_VERSION,
                "settings": settings,
                "previous_settings": old_state.get("settings") if old_state else None,
                "settings_sha256": _sha256(new_kdl.encode("utf-8")),
                "active_config_scope": scope,
                "active_config_added_include": bool((old_state or {}).get("active_config_added_include", False) or include_added),
            }
            _atomic_write(self.state_path, (json.dumps(next_state, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(),
                          uid=self.uid, mode=0o600)
        except Exception as error:
            if settings_written:
                if old_settings_bytes is None:
                    try:
                        self.settings_path.unlink()
                    except OSError:
                        pass
                else:
                    _atomic_write(self.settings_path, old_settings_bytes, uid=self.uid, mode=0o600, existing_owner=self.uid)
            if user_config_written:
                _atomic_write(config_path, old_config_bytes, uid=self.uid,
                              mode=stat.S_IMODE(config_info.st_mode), existing_owner=self.uid)
            if old_state_bytes is None:
                try:
                    self.state_path.unlink()
                except OSError:
                    pass
            else:
                _atomic_write(self.state_path, old_state_bytes, uid=self.uid, mode=0o600, existing_owner=self.uid)
            if isinstance(error, NiriSettingsError):
                raise
            raise NiriSettingsError("falha ao gravar configuração Niri+; arquivos anteriores restaurados") from error
        return {"status": "OK", "configured": True, "settings": settings, "reload": "Niri observa includes e recarrega ao salvar"}

    def rollback(self) -> dict[str, object]:
        state = self._load_state()
        if state is None or state.get("previous_settings") is None:
            raise NiriSettingsError("não há uma versão anterior de configurações Niri+ para restaurar")
        return self.apply(state["previous_settings"])


def apply_from_stdin(manager: NiriSettingsManager | None = None, *, stream=None) -> int:
    import sys

    stream = sys.stdin if stream is None else stream
    raw = stream.read(65537)
    if len(raw) > 65536:
        print(json.dumps({"status": "ERROR", "message": "pedido maior que 64 KiB"}, ensure_ascii=False))
        return 2
    try:
        payload = json.loads(raw, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid constant")))
        result = (manager or NiriSettingsManager()).apply(payload)
    except (json.JSONDecodeError, ValueError, NiriSettingsError) as error:
        print(json.dumps({"status": "ERROR", "message": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0
