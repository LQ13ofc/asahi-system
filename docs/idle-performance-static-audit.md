# Auditoria estática de performance idle e preparação A/B/C

## Escopo fechado

Esta auditoria parte de `asahi-system` em `011a409148f9b7cfb000d705742ec2e25fb44773`
(Niri+ 0.1.5) e do Quickshell **exatamente** no pin
`55e92880d0aff75d235f283c839ec0990eaa9e17`.

Ela é uma auditoria estática de custo provável, não uma medição no M1. Nenhuma
classificação abaixo deve ser convertida em ganho esperado sem o benchmark.
O gate B2 permanece fechado/KNOWN-GOOD; esta rodada não o reabre e não altera o
repositório Quickshell.

Foram inspecionados todos os QML de runtime sob `components/`, `modules/`,
`panels/` e `services/`, além de `shell.qml`, `Bar.qml`, `Theme.qml` e
os units/configs de sessão do `asahi-system`. Arquivos de teste/stubs não são
tratados como runtime.

## Árvore realmente iniciada

```text
asahi-quickshell.service
└─ /usr/bin/qs --path /usr/local/share/niri-plus/quickshell/shell.qml
   └─ ShellRoot
      ├─ Toasts                         [1 global, residente]
      │  └─ Notification toast window  [lazy: só quando há popup]
      ├─ Variants(Quickshell.screens)
      │  └─ Bar                         [1 por tela, residente]
      │     ├─ Workspaces → Niri singleton
      │     ├─ Music      → Media singleton
      │     ├─ Clock      → SystemClock(minutes)
      │     ├─ Weather    → Weather singleton
      │     ├─ Cpu/Mem    → Stats singleton
      │     ├─ Vol        → PipeWire default sink + tracker
      │     ├─ Bri        → Brightness singleton
      │     ├─ Net        → Network singleton / NetworkManager
      │     ├─ Bt         → Bluetooth.defaultAdapter
      │     ├─ Bat        → UPower.displayDevice
      │     ├─ Notif      → Notifications singleton
      │     └─ Tray       → SystemTray.items
      │
      │     Panel wrappers existem no Bar, mas o corpo é:
      │       Panel → LazyLoader(active = alive) → Loader(sourceComponent)
      │
      │     Portanto AudioPanel/BluetoothPanel/CalendarPanel/MusicPanel/
      │     NetworkPanel/NotificationPanel/TrayMenu/WeatherPanel e seus
      │     processos/timers não existem enquanto o painel está fechado.
      └─ IpcHandler("barra")             [residente, event-driven]
```

No lado do `asahi-system`, a sessão Niri ainda mantém o compositor e o agente
PolicyKit. `asahi-niri-wayland-ready.service` é `Type=oneshot`: o Python de
readiness termina após o gate e não é um processo idle residente.
`xwayland-satellite` é dependência do Niri e permanece on-demand pelo mecanismo
do compositor; `foot` e `fuzzel` só são iniciados por ação do usuário.

## Inventário de timers e periodicidade

- **< 1 s:** `Panel.qml` tem hover intent de 120 ms e close delay de 240 ms;
  `Niri.qml` começa retry em 500 ms. São timers condicionais a interação/falha,
  não loops idle. Animações de 120/180/260 ms também só executam em mudança de UI.
- **< 5 s:** não foi encontrado polling idle periódico nessa faixa. O retry do
  socket Niri pode crescer até 5 s somente quando o stream falha.
- **5 s:** `Stats.qml` recarrega `/proc/stat` e `/proc/meminfo` a cada
  `Theme.statsInterval = 5000` ms. É o único polling periódico curto do startup
  normal.
- **< 30 s:** timeouts de Bluetooth (8/20 s), notificações (6/12 s) e timeout de
  curl meteorológico (20 s) são deadlines de operações/eventos, não polling.
  Bluetooth existe apenas com o painel aberto.
- **30 s:** o timer de `Brightness.qml` só religa `udevadm monitor` se o
  processo morrer.
- **1 min:** `Clock.qml` usa `SystemClock.Minutes`; é atualização de relógio,
  não subprocesso/polling shell.
- **10 min:** `agendaTtl` é política de cache; não existe timer residente da
  agenda. A checagem ocorre ao abrir o calendário.
- **1 h:** `Weather.qml` agenda um refresh a cada 3 600 000 ms. O `curl` é
  efêmero e também pode ocorrer no startup se o cache estiver ausente/velho.

### Filesystem watchers

Nenhum QML do pin habilita `FileView.watchChanges`. No Quickshell 0.3.1 essa
opção é `false` por padrão; portanto os `FileView` encontrados são leitura/carga
explícita, não watchers permanentes. `Stats.qml` chama `reload()` pelo timer de
5 s; Brightness chama reload a partir de uevents; caches de Weather/Agenda são
lidos/escritos nos momentos descritos acima.

## Tabela de custo idle estático

| Componente | Arquivo | Startup/residente? | Mecanismo | Frequência/evento | Processo extra? | Custo idle provável | Classificação | Evidência |
|---|---|---|---|---|---|---|---|---|
| Niri | `asahi-system` / sessão | Sim | compositor Wayland | event loop | `niri` residente | PSS/CPU base da sessão | MUST_STAY_RESIDENT | sessão principal; comparação B↔C separa shell do compositor |
| Wayland readiness | `sessions/systemd/asahi-niri-wayland-ready.service` | Startup, não residente | oneshot Python | uma vez por login | Python efêmero | zero após conclusão | EVENT_DRIVEN_OK | `Type=oneshot`, `RemainAfterExit=yes` |
| PolicyKit agent | `asahi-niri-polkit-agent.service` | Sim | agente D-Bus | eventos de autenticação | `lxqt-policykit-agent` | PSS pequeno/moderado; CPU normalmente dormindo | MUST_STAY_RESIDENT | necessário para autenticação normal da sessão Niri |
| Quickshell engine | `asahi-quickshell.service` | Sim no C; ausente no B | Qt/QML event loop | eventos + timers abaixo | `qs` | candidato principal de PSS do shell | BENCHMARK_REQUIRED | B vs C mede o custo incremental real |
| Bar por tela | `shell.qml`, `Bar.qml` | Sim, 1 por tela | objetos QML/PanelWindow | reativo | não | PSS/QML proporcional a telas/módulos | BENCHMARK_REQUIRED | `Variants { model: Quickshell.screens; Bar {} }` |
| Wrappers de painel | `components/Panel.qml` + módulos | Sim, conteúdo não | Scope + bindings + Timers + LazyLoader | timers só em hover/fechamento | não | algum PSS por wrapper/tela, CPU idle baixa | SHOULD_BE_LAZY | wrapper nasce no Bar; `LazyLoader active: root.alive` impede conteúdo fechado |
| Conteúdo dos painéis | `panels/*.qml` | Não | Loader dentro do LazyLoader | só painel aberto | depende do painel | zero no idle fechado | SHOULD_BE_ON_DEMAND | objeto só é criado quando `Panel.alive` |
| CPU/mem stats | `services/Stats.qml` | Sim, singleton compartilhado | `FileView.reload()` | **polling 5 s** | não | wakeups + parsing de dois procfs; PSS baixo | SUSPICIOUS_POLLING | Timer `running:true; repeat:true`, `statsInterval=5000` |
| Niri state | `services/Niri.qml` | Sim, singleton | Socket IPC EventStream | evento; retry só após falha | não | PSS pequeno; CPU baixa quando quieto | MUST_STAY_RESIDENT | um stream persistente; ações usam sockets curtos; sem polling |
| Network state | `services/Network.qml` | Sim, singleton | Quickshell.Networking / NetworkManager D-Bus | eventos NM | não | modelo D-Bus residente; CPU baixa em idle | EVENT_DRIVEN_OK | usa `Networking.devices.values`; nenhum helper/gdbus/busctl |
| Volume da barra | `modules/Vol.qml` | Sim por tela | PipeWire default sink + `PwObjectTracker` | eventos PipeWire | não | assinatura/tracker residente | EVENT_DRIVEN_OK | não há Timer/Process |
| Áudio completo | `panels/AudioPanel.qml` | Não | modelos PipeWire + tracker de nós | só painel aberto | não | zero no idle fechado | SHOULD_BE_ON_DEMAND | conteúdo lazy do Panel |
| Bluetooth indicador | `modules/Bt.qml` | Sim por tela | BlueZ/Quickshell.Bluetooth | eventos BlueZ | não | assinatura/modelo; sem discovery idle | EVENT_DRIVEN_OK | discovery não é iniciado pelo módulo residente |
| Bluetooth discovery | `panels/BluetoothPanel.qml` | Não | BlueZ discovery + deadlines | somente pedido no painel | não | pode gerar rádio/CPU enquanto aberto | SHOULD_BE_ON_DEMAND | conteúdo lazy; timeouts 8/20/60 s são one-shot |
| Bateria | `modules/Bat.qml` | Sim por tela | UPower | eventos UPower | não | muito baixo | EVENT_DRIVEN_OK | `UPower.displayDevice`; sem polling |
| Brilho / estado | `services/Brightness.qml` | Sim, singleton | sysfs + udev event stream | eventos de backlight | **sim: `udevadm monitor` residente** | PSS de processo extra; CPU tende a dormir | BENCHMARK_REQUIRED | `Process running:true` com `udevadm monitor --subsystem-match=backlight` |
| Descoberta de backlight | `services/Brightness.qml` | Startup | `ls -1 /sys/class/backlight` | uma vez; repete só se nenhum device e houver refresh | `ls` efêmero | irrelevante após saída | EVENT_DRIVEN_OK | Process finder não permanece residente |
| Relógio | `modules/Clock.qml` | Sim por tela | `SystemClock.Minutes` | virada de minuto | não | wakeup baixo e previsível | MUST_STAY_RESIDENT | necessário ao relógio; sem Timer custom/subprocesso |
| Weather state | `services/Weather.qml` | Sim, singleton | cache + Timer + curl | startup se cache stale; depois 1 h | `curl` efêmero | PSS de dados; wakeup horário | BENCHMARK_REQUIRED | Timer 3 600 000 ms; Process só durante request |
| Busca de local do clima | `panels/WeatherPanel.qml` | Não | curl geocoding | Enter do usuário | `curl` efêmero | zero no idle fechado | SHOULD_BE_ON_DEMAND | Process sem `running:true`; iniciado por `search()` |
| MPRIS/media | `services/Media.qml` | Sim, singleton | MPRIS D-Bus + Instantiator | eventos de players | não | modelo por player; PSS dependente de nº players | EVENT_DRIVEN_OK | watcher QtObject por `Mpris.players`, sem polling |
| Capa/título música | `modules/Music.qml` | Sim por tela | bindings + Image cache | eventos MPRIS/imagem | não | PSS gráfico dependente de player/capa | BENCHMARK_REQUIRED | módulo reside; painel visualizador continua lazy |
| Cava | `panels/Visualizer.qml` | Não | Process + PipeWire, 30 FPS | só MusicPanel aberto | **`cava` enquanto aberto** | alto relativo enquanto aberto; zero idle fechado | SHOULD_BE_ON_DEMAND | Process é filho do conteúdo lazy e morre no destroy |
| Notificações | `services/Notifications.qml` | Sim, singleton | NotificationServer + Connections | eventos D-Bus | não | histórico/objetos podem crescer até limites configurados | BENCHMARK_REQUIRED | até 100 entradas/controladores; timers são deadlines, não polling |
| Toast overlay | `modules/Toasts.qml` | raiz residente; janela lazy | Variants | só quando `popups.length > 0` | não | raiz pequena; janela zero sem popup | EVENT_DRIVEN_OK | model do Variants fica vazio no idle sem popup |
| Tray/SNI | `modules/Tray.qml` | Sim por tela | SystemTray model + Repeater | eventos SNI | não | objetos por item de tray | EVENT_DRIVEN_OK | timer de 180 ms só após remoção; menu lazy |
| Tray menu | `panels/TrayMenu.qml` | Não | QsMenuOpener + one-shot settle | uma vez ao abrir | não | zero no idle fechado | SHOULD_BE_ON_DEMAND | Timer `running:true` está dentro do painel lazy e não é `repeat:true` |
| Agenda state | `services/Agenda.qml` | Lazy por uso do CalendarPanel | cache em FileView | ao abrir calendário | não sozinho | zero antes do painel | SHOULD_BE_ON_DEMAND | singleton só é demandado pelo conteúdo lazy; cache path definido em `readCache()` |
| Agenda helper | `panels/CalendarPanel.qml`, `services/agenda.py` | Não | Python + rede/iCal | abertura se TTL exige | **Python efêmero** | custo somente durante fetch/painel | SHOULD_BE_ON_DEMAND | Process e watchdog pertencem ao CalendarPanel lazy |
| Network helper | `services/NetworkControl.qml` | Não | Python `netctl.py` | vida do NetworkPanel | **Python enquanto painel aberto** | zero no idle fechado | SHOULD_BE_ON_DEMAND | `running: root.enabled`; objeto nasce dentro do NetworkPanel lazy |
| Network speed test | `netctl.py` via painel | Não | ação explícita | clique “velocidade” | efêmeros possíveis | zero no idle | SHOULD_BE_ON_DEMAND | não é disparado pelo indicador residente |
| NotificationPanel | `panels/NotificationPanel.qml` | Não | modelos locais | painel aberto | não | zero fechado | SHOULD_BE_ON_DEMAND | conteúdo de Panel |
| Workspaces | `modules/Workspaces.qml` | Sim por tela | modelo do Niri + Repeater | eventos IPC Niri | não | objetos por workspace visível | EVENT_DRIVEN_OK | deriva do singleton Niri, sem polling |
| `xwayland-satellite` | Niri/RPM | On-demand | Xwayland bridge | quando cliente X11 exige | processo quando necessário | deve ser zero em idle sem X11 | SHOULD_BE_ON_DEMAND | config Niri+ não o autostarta; collector o observa explicitamente |
| Fuzzel/Foot | `niri/keybinds.kdl` | Não | spawn em keybind | interação | processo enquanto usado | zero idle | SHOULD_BE_ON_DEMAND | apenas `Mod+Space`/`Mod+Return` |
| Portal GTK | pacote/session D-Bus | depende de ativação real | D-Bus/systemd | ativação por cliente/sessão | possível | não inferível só do pacote | BENCHMARK_REQUIRED | instalado não prova residência; collector registra processos/units |

### Por que essas classificações

- **MUST_STAY_RESIDENT:** componente necessário para o contrato básico da sessão e
  cujo desenho residente é coerente com a função (compositor, PolicyKit, relógio,
  stream IPC Niri).
- **EVENT_DRIVEN_OK:** permanece carregado, mas reage a sinais/eventos e não
  contém loop periódico de consulta. “Residente” aqui não significa “gratuito”;
  o PSS ainda será medido.
- **SHOULD_BE_LAZY:** objeto atualmente criado no startup embora sua função só
  faça sentido durante interação. É hipótese arquitetural para uma rodada futura,
  não autorização para mudar agora.
- **SHOULD_BE_ON_DEMAND:** o desenho correto é nascer apenas durante uma ação.
  Nos casos acima isso já está implementado; a auditoria confirma que não deve ser
  debitado ao idle fechado.
- **SUSPICIOUS_POLLING:** consulta periódica real que pode gerar wakeups mesmo sem
  mudança externa. Nesta revisão, o caso relevante é o polling de stats a cada 5 s.
- **BENCHMARK_REQUIRED:** a fonte mostra estado/processo/modelo residente ou custo
  ocasional plausível, mas não permite quantificar impacto. O próximo passo é medir.

## Ranking de candidatos — hipótese, não medição

### STATIC_SUSPECT: PSS

1. **`qs` + árvore QML residente por tela**, incluindo módulos e wrappers de
   painel. É o maior bloco incremental exclusivo do perfil C e será isolado por
   B↔C.
2. **Backends/modelos do Quickshell** (MPRIS, SNI, NetworkManager, PipeWire,
   BlueZ, UPower, NotificationServer) e seus objetos dependentes do estado.
3. **`udevadm monitor`**, processo externo residente filho/auxiliar do shell.
4. **Histórico/controladores de notificações**, cujo PSS depende de quantas
   notificações vivas/históricas existirem.
5. **Objetos gráficos por tela/workspace/tray/player**, dependentes do estado do
   host e número de telas.

### STATIC_SUSPECT: CPU idle / wakeups

1. **`Stats.qml` / 5 s**: única consulta periódica curta confirmada.
2. **Quickshell/Qt e backends event-driven**: não são polling, mas eventos reais
   podem gerar CPU; só delta no M1 quantifica.
3. **`SystemClock.Minutes`**: wakeup de minuto, esperado e pequeno.
4. **Weather 1 h**: wakeup raro + `curl` efêmero.
5. **`udevadm monitor`**: processo extra que deve dormir entre uevents; custo
   precisa ser medido, não presumido.

### STATIC_SUSPECT: processos extras

No C, a fonte prevê ao menos `qs` e, como filho/auxiliar do shell,
`udevadm monitor`. `curl`, `agenda.py`, `netctl.py` e `cava` são
condicionais/efêmeros ou restritos a painéis. Niri, PolicyKit, PipeWire,
WirePlumber e NetworkManager pertencem à sessão/sistema e são comparados
separadamente em A/B/C.

### MEASURED_COST

**Nenhum custo desta lista é `MEASURED_COST` nesta rodada.** O Cloud não
substitui o M1 e esta auditoria deliberadamente não converte estrutura estática em
MB, porcentagem de CPU ou wakeups. Os primeiros valores medidos serão os cinco
runs de A/B/C, com prioridade B↔C.

## Collector: escopo e contabilidade

O collector existente foi estendido em vez de criar um benchmark paralelo.

Cada run usa uma única janela de observação para obter:

- `MemAvailable`, swap total/usado e estado/uso de zswap;
- PSS, RSS, Private_Clean, Private_Dirty e SwapPss por PID via
  `/proc/PID/smaps_rollup`;
- CPU acumulado por PID e **delta de CPU time** na janela;
- minor/major faults acumulados e deltas;
- `/proc/PID/io` read/write acumulados e deltas;
- context switches por PID e deltas como pista leve de wakeups;
- PSI CPU/memory/I/O no começo/fim e delta de `total`;
- CPU system-wide por delta de `/proc/stat`, sem snapshot de `%CPU`;
- cgroup v2 e o cgroup do unit Quickshell, incluindo deltas de CPU/I/O;
- Niri, Quickshell, PipeWire, WirePlumber, NetworkManager,
  xwayland-satellite, PolicyKit, Plasma/KWin;
- descendentes do `qs` como `quickshell_auxiliary`;
- processos inesperados da sessão como inventário, sem removê-los.

### Regra de PSS sem dupla contagem

O JSON separa explicitamente:

1. **system-wide:** interfaces do kernel, como `MemAvailable`, swap e PSI;
2. **session:** soma de PSS de cada PID final com UID real do usuário **uma única
   vez**, excluindo o collector e sua cadeia de ancestrais;
3. **per-process:** uma linha por PID;
4. **components:** views/agrupamentos dos mesmos PIDs, sem somá-los novamente;
5. **cgroups:** `memory.current` e counters de cgroup são evidência separada,
   nunca adicionados ao PSS.

Assim, por exemplo, `qs` + um filho aparecem individualmente; o filho também
pode aparecer em `quickshell_auxiliary`, mas esse agrupamento não é somado ao
PSS de sessão. O cgroup captura CPU/I/O de filhos efêmeros que podem terminar
antes do snapshot final sem ser usado como uma segunda soma de memória.

## Perfis A/B/C e validação fail-closed

### A — `plasma`

O collector exige, durante toda a janela amostrada:

- `kwin_wayland >= 1`;
- `plasmashell >= 1`;
- `niri == 0`;
- `qs == 0`.

Nenhuma alteração é feita ao Plasma.

### B — `niri-core`

O mecanismo de preparação é temporário no user manager:

```text
systemctl --user mask --runtime --now asahi-quickshell.service
```

A CLI grava antes um marcador de ownership em
`$XDG_RUNTIME_DIR/niri-plus/benchmark-niri-core.json`. Ela recusa assumir um
mask que já existia, valida que o unit ficou `masked-runtime`, inativo e sem
qualquer processo `qs`. O restore só remove um mask runtime que possua esse
marcador e reinicia o unit na sessão Niri.

Isso não edita unit, não usa `disable`, não altera install state, pin ou
managed files e desaparece com o runtime/user manager. Durante a janela medida o
validator exige Niri e o agente PolicyKit presentes, Plasma/KWin ausentes, `qs == 0` em **todas** as
amostras e unit runtime-masked/inativo no início e no fim. Se `qs` aparecer
mesmo brevemente, o run falha.

### C — `niri-quickshell`

Durante toda a janela:

- Niri e o agente PolicyKit precisam estar presentes;
- Plasma/KWin precisam estar ausentes;
- precisa existir exatamente um `qs`;
- seu argv deve corresponder ao managed invocation
  `/usr/bin/qs --path /usr/local/share/niri-plus/quickshell/shell.qml`;
- `asahi-quickshell.service` deve estar `active/running`, `Result=success`,
  `NRestarts=0`;
- o PID gerenciado final precisa coincidir com `ExecMainPID`.

## Fail-closed

`--profile` é apenas a intenção do operador. O host é observado em intervalos
curtos ao longo de toda a janela. Se a assinatura real divergir, o collector
retorna erro e **não grava o arquivo `--output`** como resultado válido.

O JSON válido inclui `profile`, `profile_validation.status = "OK"` e toda a
evidência observada de cada run.
