# Protocolo de baseline e experimentos

## Coleta

`niri+ benchmark --profile ...` encaminha para o collector existente
`scripts/collect-performance-baseline`. O caminho de **coleta** é read-only:
lê `/proc`, `/sys`, cgroup v2, PSI, metadados systemd e ferramentas de
consulta. Não executa `drop_caches`, não altera sysctl, pacotes, mounts,
managed files, install state ou pin.

O único estado mutável introduzido para o experimento A/B/C é a preparação
explícita do perfil B, `niri+ benchmark --prepare-niri-core`. Ela usa um
**mask runtime** do unit Quickshell no `systemd --user` e tem restore
correspondente. Isso não é executado automaticamente pela coleta.

Cada métrica mantém um estado explícito:

- `AVAILABLE`: valor coletado;
- `UNAVAILABLE`: interface/ferramenta ausente, processo saiu ou valor inválido;
- `PERMISSION_REQUIRED`: leitura bloqueada por permissão;
- `NOT_APPLICABLE`: interface não se aplica ao host.

Ausência não equivale a zero. Memória por processo vem de
`/proc/PID/smaps_rollup`. PSS é a métrica prioritária para comparar footprint;
RSS continua disponível para diagnóstico.

## Janela de observação

Cada run abre uma janela padrão de 10 s (`--window`) e usa os mesmos contadores
antes/depois para:

- CPU time por PID;
- minor/major faults;
- I/O read/write;
- context switches;
- CPU system-wide;
- PSI CPU/memory/I/O;
- CPU/I/O do cgroup Quickshell.

Portanto `%CPU` instantâneo não é a métrica primária. O campo
`cpu_percent_one_core_delta` é derivado do delta de CPU time na janela, e o
valor bruto `cpu_seconds_delta` permanece disponível.

A validação do perfil também ocorre **durante** a janela, em amostras leves de
presença/argv. Isso impede que um run B seja aceito se `qs` aparecer no meio da
medição e desaparecer antes do final.

## Idle comparável

Para cada perfil:

1. Faça login limpo na sessão correspondente.
2. Não abra Brave, Steam nem aplicações extras. Use terminal apenas para preparar
   ou iniciar a coleta e não mantenha workload adicional concorrente.
3. Aguarde 2–3 minutos depois de o perfil estar no estado final.
4. Faça cinco runs com a mesma `--window`.
5. Compare mediana e MAD; preserve os cinco runs individuais.
6. Registre energia, rede, horário e atividade externa junto da conclusão.

Nunca use `drop_caches`. Nenhuma coleta Cloud x86_64 substitui o M1.

## Perfis experimentais obrigatórios

A comparação prioritária é **B vs C**.

### A — Plasma recovery

Faça login normal no Plasma e aguarde a estabilização.

```bash
niri+ benchmark --profile plasma --runs 5 --window 10 --output ~/benchmark-plasma.json
```

O validator exige durante toda a janela:

- `kwin_wayland` presente;
- `plasmashell` presente;
- Niri ausente;
- `qs` ausente.

O benchmark não modifica Plasma.

### B — Niri core, sem Quickshell

Entre normalmente no Niri. Como o lifecycle KNOWN-GOOD inicia Quickshell,
prepare explicitamente o estado temporário:

```bash
niri+ benchmark --prepare-niri-core
```

A preparação:

- recusa root;
- recusa sessão que não seja Niri;
- recusa assumir ownership se o unit já estava masked;
- cria marcador privado em
  `$XDG_RUNTIME_DIR/niri-plus/benchmark-niri-core.json`;
- executa `systemctl --user mask --runtime --now asahi-quickshell.service`;
- confirma `masked-runtime`, unit inativo e zero processos `qs`.

Não edita a unit instalada, não faz `disable`, não muda install state, pin,
managed files ou pacote. Depois da preparação, aguarde novamente 2–3 minutos e
colete:

```bash
niri+ benchmark --profile niri-core --runs 5 --window 10 --output ~/benchmark-niri-core.json
```

O validator exige Niri e o agente PolicyKit presentes, `qs == 0` em todas as amostras da janela,
além do mask runtime/inatividade no início e no fim. Se Quickshell aparecer
durante a janela, o run é inválido.

Ao terminar B, restaure:

```bash
niri+ benchmark --restore-niri-core
```

O restore só remove o mask runtime se o marcador de ownership do benchmark for
válido. Depois do unmask, o marcador é removido antes da tentativa de restart;
assim uma falha de restart não deixa ownership falso. Em Niri, a CLI tenta
reiniciar o unit e informa erro de forma explícita se isso falhar.

### C — Niri + Quickshell

Use a sessão Niri normal, depois de restore/login limpo:

```bash
niri+ benchmark --profile niri-quickshell --runs 5 --window 10 --output ~/benchmark-niri-quickshell.json
```

O validator exige durante toda a janela:

- Niri presente;
- Plasma/KWin ausentes;
- agente PolicyKit presente;
- exatamente um `qs`;
- o argv gerenciado esperado:
  `/usr/bin/qs --path /usr/local/share/niri-plus/quickshell/shell.qml`;
- unit Quickshell `active/running`;
- `Result=success`;
- `NRestarts=0`;
- PID final igual a `ExecMainPID`.

## Fail-closed e arquivo de resultado

O collector não confia no nome passado a `--profile`. Se a sessão real não
corresponder ao perfil solicitado, ele retorna status de erro e não grava o
`--output` como captura aparentemente válida.

Um resultado válido contém, no topo:

```json
{
  "profile": "niri-quickshell",
  "profile_validation": {
    "status": "OK",
    "runs": []
  }
}
```

Cada run preserva a timeline/evidência usada na validação.

## Memória sem dupla contagem

O JSON separa quatro perspectivas principais:

- `memory`: system-wide (`MemAvailable`, swap, cache etc.);
- `memory_scopes.session`: footprint da sessão;
- `session.processes`: métricas por PID;
- `components`: views como Niri, Quickshell, PipeWire, WirePlumber,
  NetworkManager e xwayland-satellite;
- `systemd_user_cgroup`: cgroup do manager `systemd --user`, separado do PSS;
- `quickshell_cgroup`: cgroup específico do unit Quickshell.

O **PSS agregado da sessão** é a soma de PSS dos PIDs únicos do snapshot final
cujo UID real é o usuário do benchmark, excluindo o collector e sua cadeia de
ancestrais. Cada PID entra uma vez. Os grupos de `components` são views dos
mesmos PIDs e nunca são somados novamente ao total da sessão.

`quickshell_cgroup` é mantido à parte. `memory.current` não é somado ao PSS.
O cgroup é usado principalmente para CPU/I/O e para capturar atividade de filhos
efêmeros que podem terminar antes do snapshot final.

## Componentes selecionados e processos inesperados

O collector expõe grupos para:

- Niri;
- Quickshell;
- PipeWire / pipewire-pulse;
- WirePlumber;
- NetworkManager;
- xwayland-satellite;
- agente PolicyKit;
- Plasma/KWin;
- descendentes do Quickshell em `quickshell_auxiliary`.

Também registra um inventário `unexpected_session_processes`. Esse inventário é
observacional: não autoriza matar, desabilitar ou remover serviços.

## Interpretação

`used_bytes = MemTotal - MemAvailable` é apenas a visão system-wide de memória
não disponível sem pressão. Não é soma de RSS.

A soma de PSS da sessão e os PSS dos componentes são úteis para A/B/C, mas ainda
podem ficar parciais se o kernel negar `smaps_rollup` de algum PID. Nessa
situação a métrica permanece explicitamente `UNAVAILABLE`/incompleta, em vez de
fingir zero.

O custo do próprio collector existe e deve ser mantido idêntico entre perfis.
Se um resultado for ambíguo, a decisão do experimento é `INCONCLUSIVE`, não uma
otimização baseada em inferência.
