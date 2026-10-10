# Gaming Mode

Gaming Mode is an explicit launcher inside the active Niri session. It exists
only while Gamescope wraps the requested command:

```text
Niri → Gamescope → Steam or game
```

Use `niri+ gaming status` to inspect the current session and executable
availability. `AVAILABLE_UNVERIFIED` only means that the session markers and
Gamescope binary are present; it does not claim that rendering works on Asahi.
Start a command with `niri+ gaming run -- <program> [arguments]`
or start Steam on demand with `niri+ gaming steam`. The launcher requires the
current user's live Niri socket, Wayland display, and Niri desktop marker. It
does not run games as root.

If Gamescope is absent or cannot start, Niri+ returns an error and does not
retry the command directly in Niri. A game launched normally outside this
command is ordinary Niri use, not Gaming Mode. The launcher uses only the
standard `--` command separator; it does not invent resolution, frame-rate,
GPU, or cgroup flags and does not install Gamescope or Steam.

Steam and games are launched only when requested. ARM64-native games remain
host processes. The existing Steam → muvm → FEX → Proton path is not modified or
tuned by this command. Runtime compatibility with Honeykrisp/Niri, suspend and
resume, frame pacing, and resource behavior remain `M1_REQUIRED`; in particular,
Cloud does not claim that Gamescope currently works on the target hardware.
