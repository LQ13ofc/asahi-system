# Primeiro teste experimental final no MacBook Air M1

Este procedimento é somente para a branch/PR candidata final depois que o
head, o lock Quickshell e o checksum do bootstrap forem fixados. O novo fluxo
não está no Niri+ 0.1.9 instalado atualmente. Não execute `--with-quickshell`
com essa versão. O teste é reversível e continua diferente de aprovação de
produção. Plasma permanece selecionável no SDDM.

## A. Pré-requisitos

- MacBook Air M1 2020, 8 GiB, Fedora Asahi Remix 44, aarch64.
- Energia conectada; acesso ao Plasma e a um TTY já testados anteriormente.
- Espaço livre suficiente para o pacote de recovery e os RPMs candidatos.
- Acesso GitHub autorizado a `LQ13ofc/asahi-system` e ao repositório privado
  `LQ13ofc/quickshell-` (a autenticação será não interativa).
- Anote somente os commits e hashes publicados na seção de release do PR RC
  novo. Não use hashes do PR #22 ou do procedimento histórico.
- Se os caminhos do clone/temporários deste guia já existirem, escolha nomes
  novos. O procedimento nunca sobrescreve checkout ou download anterior.
- Feche aplicações e faça backup normal dos dados pessoais importantes.

## B. Preparação do recovery offline

O Niri+ 0.1.9 atualmente instalado não tem `preflight`, `plugin list` ou
`recovery`. Antes de buscar o RC, use somente os diagnósticos que essa versão
oferece:

```sh
niri+ status
niri+ doctor
```

O dry-run do bootstrap RC é a pré-validação read-only de host e commits. No
primeiro `--apply`, o instalador novo prepara e verifica automaticamente o
recovery offline em `/var/lib/niri-plus/recovery` antes de alterar arquivos,
integrações ou pacotes. Se o bundle não puder ser preparado/verificado, a
instalação aborta antes da primeira mutação. Depois de uma aplicação bem
sucedida, confira o bundle e o preflight com os comandos novos:

```sh
niri+ preflight
niri+ recovery status
```

O bundle fica protegido localmente; não o envie junto com diagnósticos.

## C. Migração e compatibilidade do 0.1.9

O CLI 0.1.9 não possui a interface `niri+ plugin`. Não tente usá-la antes do
bootstrap RC. O candidato detecta automaticamente a integração 0.1.9 somente
se pin, runtime, hashes e unidades antigas puderem ser validados. Caso a
migração não seja verificável, ele deve parar sem assumir ownership. Uma
instalação independente de `qs` não ativa o plugin.

## D. Commits e hashes

Use um clone novo e pertencente ao usuário normal. Os comandos abaixo obtêm a
head aberta do PR #24 sem trocar de branch e conferem o gitlink e lockfile com
o pin aprovado do Quickshell. `git fetch` não executa arquivos do checkout como
root:

```sh
export RC_PR=24
export RC_QUICKSHELL_COMMIT=d2fe6d57dc2b86e5f433a0f6282732b8d03ca7f3
export RC_SOURCE="$HOME/Projects/niri-plus-release-source"
export RC_DIR="$HOME/.cache/niri-plus-release"
test ! -e "$RC_SOURCE"
test ! -e "$RC_DIR"
mkdir -p "$HOME/Projects"
install -d -m 700 "$RC_DIR"
git clone --branch main --single-branch https://github.com/LQ13ofc/asahi-system.git "$RC_SOURCE"
test "$(git -C "$RC_SOURCE" remote get-url origin)" = https://github.com/LQ13ofc/asahi-system.git
GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never git -C "$RC_SOURCE" fetch --no-tags origin "refs/pull/$RC_PR/head"
export RC_SYSTEM_COMMIT="$(git -C "$RC_SOURCE" rev-parse --verify 'FETCH_HEAD^{commit}')"
test "$(GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never git -C "$RC_SOURCE" ls-remote --exit-code --refs origin "refs/pull/$RC_PR/head" | awk '{print $1}')" = "$RC_SYSTEM_COMMIT"
test "$(git -C "$RC_SOURCE" rev-parse "$RC_SYSTEM_COMMIT:external/quickshell")" = "$RC_QUICKSHELL_COMMIT"
test "$(git -C "$RC_SOURCE" show "$RC_SYSTEM_COMMIT:integration/quickshell.lock.json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["commit"])')" = "$RC_QUICKSHELL_COMMIT"
```

Calcule o hash do bootstrap a partir do blob dentro desse commit e compare o
download do mesmo commit. Não use um checksum de outro head:

```sh
RC_BOOTSTRAP_BLOB="$(git -C "$RC_SOURCE" rev-parse "$RC_SYSTEM_COMMIT:scripts/niri-plus-rc-bootstrap")"
export RC_BOOTSTRAP_SHA256="$(git -C "$RC_SOURCE" cat-file blob "$RC_BOOTSTRAP_BLOB" | sha256sum | awk '{print $1}')"
curl --fail --location --proto '=https' --tlsv1.2 "https://raw.githubusercontent.com/LQ13ofc/asahi-system/$RC_SYSTEM_COMMIT/scripts/niri-plus-rc-bootstrap" --output "$RC_DIR/niri-plus-rc-bootstrap"
printf '%s  %s\n' "$RC_BOOTSTRAP_SHA256" "$RC_DIR/niri-plus-rc-bootstrap" | sha256sum --check
```

O canal normal continua em `main` e o pin de produção em
`55e92880d0aff75d235f283c839ec0990eaa9e17`. Somente o helper opt-in abaixo
instala o head RC exato e o Quickshell `d2fe6d57dc2b86e5f433a0f6282732b8d03ca7f3`.

## E. Dry-run experimental

O verificador lê o helper uma vez, confere seu SHA-256 e executa somente esses
bytes em memória como root. O bootstrap então valida o objeto do helper,
resolução do PR, lock, gitlink, manifestos e origem antes de mostrar o plano:

```sh
niri_plus_rc() {
    sudo python3 -I -c '
import hashlib, pathlib, sys
args = sys.argv[1:]
path = pathlib.Path(args[0])
expected = args[1]
payload = path.read_bytes()
if hashlib.sha256(payload).hexdigest() != expected:
    raise SystemExit("RC bootstrap SHA-256 mismatch; nothing was executed")
sys.argv = [str(path), *args[2:]]
exec(compile(payload, str(path), "exec"), {
    "__name__": "__main__",
    "__file__": str(path),
    "__verified_script_bytes__": payload,
})
' "$RC_DIR/niri-plus-rc-bootstrap" "$RC_BOOTSTRAP_SHA256" \
      --source-root "$RC_SOURCE" --bootstrap-commit "$RC_SYSTEM_COMMIT" "$@"
}
niri_plus_rc --candidate-commit "$RC_SYSTEM_COMMIT" --dry-run
```

Se não havia plugin gerenciado, o plano deve dizer que Quickshell não foi
buscado. Para incluir a primeira instalação visual no teste, faça também este
dry-run e confirme a mesma head de sistema e o pin exato:

```sh
niri_plus_rc --candidate-commit "$RC_SYSTEM_COMMIT" --with-quickshell --dry-run
```

Se o manifesto Quickshell já existe, o RC inclui e atualiza o plugin
automaticamente. O binário independente `qs` sozinho não ativa o plugin. O
comando `sudo niri+ install --dry-run` continua sendo produção (`main`) e não é
o dry-run do RC; numa versão RC mais nova que `main`, ele recusará downgrade.

## F. Instalação do candidato

Execute o update no Plasma ou TTY, com a sessão gráfica encerrada. Se Quickshell
estiver ativo ou seu estado de processo não puder ser verificado, o bootstrap
recusa a atualização antes de alterar o host. Com plugin gerenciado, o comando
abaixo atualiza core e Quickshell juntos; sem plugin, instala somente o core:

```sh
niri_plus_rc --candidate-commit "$RC_SYSTEM_COMMIT" --apply
```

Para opt-in inicial ao Quickshell durante este RC experimental, use em vez
disso:

```sh
niri_plus_rc --candidate-commit "$RC_SYSTEM_COMMIT" --with-quickshell --apply
```

No fluxo normal de produção, a primeira instalação permanece
`sudo niri+ plugin install quickshell`; o bootstrap RC explícito não altera
esse contrato. Não use `sudo niri+ install` para aplicar ou atualizar este RC.

Não interrompa o DNF. Se rede, credenciais, RPM, espaço ou validação do bundle
falharem, pare e use a recuperação local somente depois de conferir seu status.

## G. Entrar na sessão Niri

Encerre a sessão atual e selecione **Niri** no SDDM. Plasma deve continuar
aparecendo como alternativa. Se a tela ou autenticação falhar, volte ao Plasma
ou TTY; não desinstale pacotes manualmente.

## H. Barra, Settings Center e Control Center

Na sessão Niri, confirme barra única no monitor esperado, abrir/fechar Settings
Center e Control Center, navegação por teclado, tema/preferências carregadas e
ausência de painel duplicado em monitores conectados. Verifique:

```sh
niri+ status
niri+ plugin status quickshell
niri+ doctor
```

## I. Rede, áudio, brilho e entrada

Faça testes funcionais curtos: reconexão Wi-Fi, Bluetooth se disponível,
reprodução/volume/mute, brilho, teclado, trackpad e atalhos. Suspend/resume e
retorno ao Plasma são verificações separadas. Não desligue NetworkManager,
PipeWire/WirePlumber, speakersafetyd ou componentes de segurança.

## J. CPU, RAM, PSS e desempenho

Faça login limpo, não abra browser/Steam/terminal extra, espere 2–3 minutos e
execute uma vez o coletor para cada perfil. Os comandos fazem cinco runs de
dez segundos e gravam em diretórios novos modo 0700:

```sh
/usr/local/share/niri-plus/scripts/collect-m1-release-gate.sh niri-core
/usr/local/share/niri-plus/scripts/collect-m1-release-gate.sh niri-quickshell
```

O baseline Plasma pode ser coletado separadamente quando estiver na sessão
Plasma:

```sh
/usr/local/share/niri-plus/scripts/collect-m1-release-gate.sh plasma
```

O JSON de memória pode conter argv com tokens, caminhos, nomes de processos,
PIDs e metadados de sessão. Os arquivos não são enviados automaticamente;
revise e redija-os antes de compartilhar. Não use `drop_caches`.

## K. Estado de Gaming Mode

Confirme somente a disponibilidade:

```sh
niri+ gaming status
```

Não inicie Steam, Gamescope nem jogos nesta rodada.

## L. Recuperação offline via Plasma

Se o shell candidate estiver inutilizável, encerre a sessão e entre no Plasma.
Desconecte a rede opcionalmente para confirmar independência:

```sh
sudo /usr/local/libexec/niri-plus-recover status
sudo /usr/local/libexec/niri-plus-recover restore
```

Depois de reiniciar ou encerrar a sessão, confira o status e selecione a sessão
Niri conhecida boa. Configurações Niri existentes antes do restore ficam
preservadas em `preserved-edits-*`.

## M. Recuperação offline via TTY

Troque para um TTY, entre com seu usuário e execute os mesmos comandos com
`sudo`. Se Quickshell estiver ativo, finalize a sessão gráfica primeiro. O
helper não requer `niri+`, GitHub, internet, `qs` nem compositor funcional.

## N. Critérios PASS/FAIL

**PASS**: commits/hashes conferem; dry-run identifica os dois commits quando o
plugin está gerenciado; instalação termina sem erro; Plasma permanece no SDDM;
Niri abre e fecha normalmente; Quickshell tem uma instância; Settings/Control
Center, rede, áudio, brilho e entrada funcionam; diagnostics mostram pins
coerentes; recovery offline valida bundle e retorna aos arquivos known-good.

**FAIL**: hash divergente; prompt de credenciais; mismatch entre gitlink/lock;
estado saudável falso; falha parcial sem recuperação; perda de Plasma/SDDM;
Niri não inicia/fecha; múltiplas instâncias; regressão de input, rede ou áudio;
bundle inválido. Em FAIL, não repita a instalação; preserve diagnósticos
privados e recupere do Plasma/TTY.

## O. Escopo do recovery

O bundle restaura paths gerenciados Niri+ e a engine Quickshell anterior somente
quando o RPM cacheado e assinado estiver disponível. O inventário inclui
NEVRAs/requisitos observáveis, mas não espelha dependências Fedora nem reverte
kernel, Mesa, serviços globais ou todos os RPMs. Uma recuperação de arquivos
não prova compatibilidade de GPU ou desempenho; essas evidências são
`M1_REQUIRED`.
