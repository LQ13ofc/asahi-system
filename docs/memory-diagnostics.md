# Diagnóstico de memória do Niri+

## O que significa “4 GiB usados”

Os 4 GiB relatados pelo usuário são uma observação do Mac, ainda sem uma
captura que atribua esse valor. `MemTotal - MemAvailable` é uma estimativa
global da memória que não está prontamente disponível para novas alocações. Não
é PSS do Niri, não é a soma de RSS e não mede memória dedicada à GPU. Sem a
captura do M1, não é possível concluir se a diferença vem de processos, cache,
kernel, buffers gráficos ou da forma como a métrica foi lida.

O baseline histórico checked-in foi capturado em 2026-10-06 numa sessão
Fedora Asahi Remix 44 com Plasma, não em Niri clean idle. Nele,
`MemTotal=7,681,376 kB`, `MemAvailable=2,159,024 kB` e a subtração resulta em
`5,522,352 kB` (aprox. 5.27 GiB). O mesmo instantâneo registra `Cached` em
3.13 GiB, `Shmem` em 1.02 GiB, `AnonPages` em 2.72 GiB, swap usada em zero e
vários processos Brave e Plasma. Essas categorias se sobrepõem e não podem ser
somadas; são evidência de que o baseline antigo tinha muita memória de processo,
cache e compartilhada, mas não explicam a observação atual de 4 GiB em Niri.
Esse arquivo não é uma medição B ou C e não permite atribuir bytes ao Niri,
Quickshell ou GPU.

## Comandos

O relatório pontual é read-only em relação ao sistema e não exige root:

```bash
niri+ memory
niri+ memory --json-output "$HOME/niri-memory.json"
```

A coleta longitudinal inicia uma amostra imediatamente e encerra depois de
quatro amostras (+0, +5, +15 e +30 minutos). `--include-60-minutes` acrescenta
a última amostra opcional. Cada amostra completa é gravada atomicamente em um
arquivo lateral `*.partial`; o destino anterior só é substituído quando a
série termina. Se a coleta for cancelada, o destino anterior continua intacto
e a série parcial é mantida no caminho informado pelo comando:

```bash
niri+ memory --series --json-output "$HOME/niri-memory-series.json"
niri+ memory --series --include-60-minutes --json-output "$HOME/niri-memory-series.json"
```

O processo não fica residente depois do comando. `--window` (padrão 2 s) e
`--sample-period` (padrão 0,5 s) controlam a pequena janela de CPU por processo.
O arquivo JSON de destino é a única escrita solicitada pelo usuário; o coletor
não altera serviços, pacotes, configurações ou políticas de memória.

### Privacidade do JSON

Antes de escrever JSON, a CLI mostra um aviso. O arquivo inclui `argv` por
processo, que pode conter tokens ou outros segredos, além de caminhos de
executáveis/cgroups, PID, UID, nomes de processos e metadados da sessão. O JSON
também carrega um campo `privacy.review_before_sharing`; inspecione e redija os
dados antes de compartilhar o arquivo fora de um ambiente confiável. O coletor
não tenta adivinhar quais argumentos ou caminhos são secretos.

## Contabilidade e limites

- **Global:** coleta `MemTotal`, `MemAvailable`, `MemFree`, `Buffers`, `Cached`,
  `Shmem`, `SReclaimable`, `SUnreclaim`, `Slab`, `AnonPages`, `Mapped`,
  `KernelStack`, `PageTables`, `Unevictable`, `Active`, `Inactive` e swap.
  `Slab` já contém `SReclaimable` e `SUnreclaim`; não some o total com as partes.
  Campos de `meminfo` podem se sobrepor entre si e com PSS.
- **Processos:** `smaps_rollup` fornece PSS, RSS, `Private_Clean`,
  `Private_Dirty` e `SwapPss` por PID. `Private_Clean + Private_Dirty` é mostrado
  como USS aproximado. Cada processo aparece no inventário, mesmo sem nome
  conhecido; o ranking deixa métricas indisponíveis explícitas. O PSS de um grupo
  pode continuar disponível quando todos os seus PIDs são legíveis, mesmo que o
  Cloud bloqueie `smaps_rollup` de processos alheios; o total global de PSS exige
  cobertura completa. Um componente ausente só recebe `NOT_APPLICABLE` quando a
  enumeração de PIDs foi completa; se algum PID não pôde ser identificado, sua
  ausência permanece `UNAVAILABLE`.
- **Grupos de processo:** `GRAPHICS_COMPONENTS` é uma vista de Niri, Quickshell,
  auxiliares e Xwayland. Serviços em `system.slice` são outra vista. O proxy
  `SAME_UID_USER_PROCESSES` inclui serviços de usuário e aplicativos. A coleta
  não prova o limite exato do cgroup da sessão gráfica e informa
  `graphical_session_processes: NOT_ACCOUNTED`; não trate o proxy por UID como
  uma partição exata da sessão.
- **Cgroups:** `memory.current`, `memory.stat`, `memory.events` e pressão são
  evidência separada. Cgroup inclui páginas que também podem estar representadas
  nos processos; nunca some `memory.current` ao PSS.
- **Residual:** `MemTotal - MemAvailable - PSS total` é um residual aritmético
  aproximado com instantes e domínios de contabilidade distintos. Não o rotule
  como kernel, cache ou GPU. Um resultado negativo é preservado como sinal da
  diferença entre domínios/instantes, não é truncado para zero e não é atribuído
  a um dispositivo.
- **PSI/swap:** pressão vem de `/proc/pressure`; swap e zswap são mostrados
  separadamente. Ausência de debugfs ou de interface resulta em
  `UNAVAILABLE`, `PERMISSION_REQUIRED`, `NOT_APPLICABLE` ou `NOT_ACCOUNTED`,
  nunca em zero inventado.

### Memória gráfica Apple Silicon

O M1 usa memória física unificada; não há um pool de VRAM dedicado a somar à
RAM. DRM `fdinfo` pode expor contadores por cliente, mas isso depende do driver e
os buffers reportados podem estar respaldados pela mesma DRAM já contada no
sistema e no PSS. O coletor desduplica `drm-client-id` e publica esses bytes
separadamente. Sem contadores, informa `NOT_ACCOUNTED` ou `NOT_APPLICABLE`;
não presume que o valor ausente seja zero.

Antes e depois de percorrer os `fdinfo`, o coletor compara `start_time_ticks`
com o processo observado para não atribuir descritores de um PID reutilizado a
outro processo. Se a identidade não puder ser comprovada, a vista DRM fica
`UNAVAILABLE` sem agregado numérico.

Na revisão Cloud, foram inspecionados os 22 arquivos Rust do driver DRM Asahi
no commit público `77cb8f24c2381a8abb7272d7bbdec548d6426a8a`; não foi encontrado
hook ou contador `fdinfo` específico do driver. A API genérica só publica
contadores residentes quando o driver os fornece. O kernel Fedora Asahi pode
ter patches ou revisão diferente; somente uma captura no Mac determina quais
campos aparecem. Referências: [formato DRM fdinfo do kernel](https://docs.kernel.org/gpu/drm-usage-stats.html)
e [fonte Asahi no commit inspecionado](https://github.com/AsahiLinux/linux/tree/77cb8f24c2381a8abb7272d7bbdec548d6426a8a/drivers/gpu/drm/asahi).

## Crescimento no tempo

Cada amostra inclui inventário/PSS, Niri, Quickshell, filhos, contagem de
processos e threads, CPU, memória disponível, swap, PSI e cgroups. A análise
offline reporta deltas e monotonicidade. Só marca um processo como
`POSSIBLE_LEAK_CANDIDATE` quando o mesmo par observado PID + `start_time_ticks`
e os metadados de processo (nome, UID, executável e cgroup) permanecem
consistentes, PSS/private-dirty não decrescem em todas as quatro amostras e ao
menos um deles cresce 1 MiB ou mais. Crescimentos monotônicos menores continuam
visíveis em `subthreshold_process_growth`, sem serem chamados de vazamento. O
limite de 1 MiB é um filtro heurístico acima do arredondamento de smaps, não foi
calibrado no M1 e não prova que crescimentos maiores sejam vazamentos. PID
reutilizado com outro `start_time_ticks`, mudança de metadados, processos
que desaparecem e métricas PSS incompletas não geram candidato. O campo é uma
pista, nunca diagnóstico: `start_time_ticks` tem resolução de ticks do kernel e
não é uma prova criptográfica de identidade; carga funcional, objetos retidos
e cache precisam ser investigados antes de chamar isso de vazamento.

O texto do relatório rotula bytes, percentuais de PSI/zswap, contagens e
durações separadamente. Contadores de cgroup e `memory.current` são vistas
separadas e não devem ser somados a PSS ou à estimativa global.

Uma execução sem Niri registra `NOT_APPLICABLE` para a meta idle Niri. A marca
`HIGH_BASELINE_CANDIDATE` significa apenas que a estimativa global ultrapassa a
meta de projeto de 2 GiB; não identifica causa nem justifica reduzir cache.

## Protocolo A/B/C

O coletor e comparador existentes continuam sendo a fonte para os perfis
Plasma (A), Niri sem Quickshell (B) e Niri + Quickshell (C). Faça login limpo,
aguarde 2–3 minutos, feche browser/Steam/terminal extra e colete cinco runs em
cada perfil. Priorize B↔C para custo incremental do Quickshell e A↔B para o
custo da sessão. Não use `drop_caches`. Os resultados do Cloud validam parsing,
schema e tratamento de ausência; eles não validam RAM, GPU, idle ou desempenho
do MacBook M1.
