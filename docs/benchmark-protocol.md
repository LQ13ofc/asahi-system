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

O marcador privado em `$XDG_RUNTIME_DIR` usa escrita atômica e serializa
prepare/restore. Se o restore falhar, o marcador permanece com a fase e o erro
para permitir uma nova tentativa. O código não adota nem encerra um processo
Quickshell que tenha aparecido fora da operação.

Cada métrica mantém um estado explícito:

- `AVAILABLE`: valor coletado;
- `UNAVAILABLE`: interface/ferramenta ausente, processo saiu ou valor inválido;
- `PERMISSION_REQUIRED`: leitura bloqueada por permissão;
- `NOT_APPLICABLE`: interface não se aplica ao host.

Ausência não equivale a zero. Memória por processo vem de
`/proc/PID/smaps_rollup`. PSS é a métrica prioritária para comparar footprint;
RSS continua disponível para diagnóstico.
O leitor confirma o `starttime` de `/proc/PID/stat` antes e depois de ler os
arquivos do processo. Se o PID for reutilizado no meio da leitura, aquela linha
é descartada e o inventário/PSS agregado é marcado como incompleto.

## Janela de observação

Cada run abre uma janela padrão de 10 s (`--window`). A janela de CPU/PSI é
delimitada por contadores antes/depois da timeline; snapshots completos de
processos são feitos em uma janela separada. O coletor registra:

- CPU time por PID;
- minor/major faults;
- I/O read/write;
- context switches;
- CPU system-wide;
- PSI CPU/memory/I/O;
- CPU/I/O do cgroup Quickshell.

As varreduras completas de `/proc/PID` e `smaps_rollup` ficam fora dos deltas de
CPU/PSI/cgroup: a captura inicial e as leituras de cgroup ocorrem antes dos
contadores; as leituras finais ocorrem depois. As porcentagens por processo
usam `process_observation_window_seconds`, que cobre as duas capturas e é
registrado separadamente. A timeline leve de presença de processos permanece
dentro da janela para validar o perfil. Assim, o custo das leituras completas
de `smaps_rollup` não é contabilizado como carga da sessão medida.

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

## Comparação offline e overhead do collector

Depois das três capturas:

```bash
niri+ benchmark compare \
  --plasma ~/benchmark-plasma.json \
  --niri-core ~/benchmark-niri-core.json \
  --niri-quickshell ~/benchmark-niri-quickshell.json \
  --output ~/benchmark-report.md \
  --json-output ~/benchmark-report.json
```

O comparador valida schema, perfil, evidência de perfil, read-only e metadados
de compatibilidade. Ele compara release/arquitetura/modelo, page size, kernel,
sessão Wayland, Niri+, Niri, Quickshell e o commit esperado/instalado do
Quickshell. Um mismatch torna as diferenças `INCONCLUSIVE`; campos necessários
ausentes deixam a comparabilidade `UNVERIFIED`. O commit Git de `asahi-system`
é mostrado quando disponível, mas pode estar ausente numa instalação sem
metadados Git.

Para cada métrica são exibidos mediana, MAD, mínimo, máximo, delta absoluto e
percentual. Uma classificação `MEASURED_INCREASE` ou `MEASURED_DECREASE`
exige pelo menos três runs completos e uma diferença maior que duas vezes o
maior MAD entre os lados. Isso é uma heurística descritiva conservadora, não um
teste formal de significância. O protocolo continua recomendando cinco runs.
O relatório separa memória global, PSS por sessão/processo e cgroups, e lista
processos presentes só em C e os maiores PSS.

O overhead do collector é medido separadamente:

```bash
niri+ benchmark --diagnose-overhead --runs 3 --output ~/collector-overhead.json
```

Esse modo usa janela de workload zero e registra tempo/CPU do processo,
leituras de `/proc`, leituras de `smaps_rollup`, subprocessos, PSS/I/O próprio
e contadores de faults/context switches. A estimativa nunca é subtraída dos
resultados reais. A medição do overhead deve usar o mesmo host e ferramentas
que as capturas; Cloud é apenas `CLOUD_MEASURED`.

## C0/C1 — Quickshell KNOWN-GOOD contra candidato

Depois de validar um candidato em branch e preparar uma instalação experimental
revisável, capture cinco runs de cada revisão com o mesmo perfil Niri normal:

```bash
niri+ benchmark --profile niri-quickshell --runs 5 --window 10 --output ~/quickshell-c0.json
# Troque para o candidato com o procedimento experimental aprovado; nunca rode duas instâncias.
niri+ benchmark --profile niri-quickshell --runs 5 --window 10 --output ~/quickshell-c1.json
niri+ benchmark compare-quickshell \
  --known-good ~/quickshell-c0.json \
  --candidate ~/quickshell-c1.json \
  --output ~/quickshell-c0-c1.md \
  --json-output ~/quickshell-c0-c1.json
```

O comparador exige que cada captura use `niri-quickshell`, passe sua validação
de lifecycle e tenha commits esperado/instalado iguais dentro daquela captura.
O hash Quickshell pode diferir entre C0 e C1, e fica registrado como a variável
do experimento. Versão Quickshell, Niri+, compositor, kernel, host, janela e
cadência ainda precisam coincidir. Se a revisão do `asahi-system` também mudou,
o resultado fica inconclusivo por padrão. Só use
`--allow-asahi-system-commit-change` depois de revisar que o diff seleciona o
pin candidato e não altera outra variável de sessão; o relatório registra essa
exceção.

Este comando só compara arquivos offline. Ele não instala nem troca revisões,
não altera o pin KNOWN-GOOD e não valida equivalência visual no Cloud.

## C0/C1 — Quickshell KNOWN-GOOD contra candidato

Depois de validar um candidato em branch e preparar uma instalação experimental
revisável, capture cinco runs de cada revisão com o mesmo perfil Niri normal:

```bash
niri+ benchmark --profile niri-quickshell --runs 5 --window 10 --output ~/quickshell-c0.json
# Troque para o candidato com o procedimento experimental aprovado; nunca rode duas instâncias.
niri+ benchmark --profile niri-quickshell --runs 5 --window 10 --output ~/quickshell-c1.json
niri+ benchmark compare-quickshell \
  --known-good ~/quickshell-c0.json \
  --candidate ~/quickshell-c1.json \
  --output ~/quickshell-c0-c1.md \
  --json-output ~/quickshell-c0-c1.json
```

O comparador exige que cada captura use `niri-quickshell`, passe sua validação
de lifecycle e tenha commits esperado/instalado iguais dentro daquela captura.
O hash Quickshell pode diferir entre C0 e C1, e fica registrado como a variável
do experimento. Versão Quickshell, Niri+, compositor, kernel, host, janela e
cadência ainda precisam coincidir. Se a revisão do `asahi-system` também mudou,
o resultado fica inconclusivo por padrão. Só use
`--allow-asahi-system-commit-change` depois de revisar que o diff seleciona o
pin candidato e não altera outra variável de sessão; o relatório registra essa
exceção.

Este comando só compara arquivos offline. Ele não instala nem troca revisões,
não altera o pin KNOWN-GOOD e não valida equivalência visual no Cloud.

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
válido. Em Niri, ele restaura e verifica a única instância gerenciada. Se o
unmask, restart ou verificação falhar, mantém o marcador `restore-required`;
uma nova execução pode retomar a recuperação com segurança.

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
