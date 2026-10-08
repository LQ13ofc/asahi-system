# Diagnóstico de memória do Niri+

## O que significa “4 GiB usados”

Os 4 GiB relatados pelo usuário são uma observação do Mac, ainda sem uma
captura que atribua esse valor. `MemTotal - MemAvailable` é uma estimativa
global da memória que não está prontamente disponível para novas alocações. Não
é PSS do Niri, não é a soma de RSS e não mede memória dedicada à GPU. Sem a
captura do M1, não é possível concluir se a diferença vem de processos, cache,
kernel, buffers gráficos ou da forma como a métrica foi lida.

## Comandos

O relatório pontual é read-only em relação ao sistema e não exige root:

```bash
niri+ memory
niri+ memory --json-output "$HOME/niri-memory.json"
```

A coleta longitudinal inicia uma amostra imediatamente e encerra depois de
quatro amostras (+0, +5, +15 e +30 minutos). `--include-60-minutes` acrescenta
a última amostra opcional. O JSON é atualizado atomicamente depois de cada
amostra completa; `Ctrl+C` preserva as que terminaram:

```bash
niri+ memory --series --json-output "$HOME/niri-memory-series.json"
niri+ memory --series --include-60-minutes --json-output "$HOME/niri-memory-series.json"
```

O processo não fica residente depois do comando. `--window` (padrão 2 s) e
`--sample-period` (padrão 0,5 s) controlam a pequena janela de CPU por processo.
O arquivo JSON de destino é a única escrita solicitada pelo usuário; o coletor
não altera serviços, pacotes, configurações ou políticas de memória.

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
  cobertura completa.
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
  como kernel, cache ou GPU.
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

Na revisão Cloud de 2026-10-08, a árvore pública Asahi consultada não tinha um
`show_fdinfo` para o driver DRM Asahi. Isso não prova qual interface existe no
kernel Fedora Asahi instalado no Mac: essa diferença só pode ser resolvida por
uma captura real. Referências: [formato DRM fdinfo do kernel](https://docs.kernel.org/gpu/drm-usage-stats.html)
e [driver Asahi público](https://github.com/AsahiLinux/linux/tree/asahi/drivers/gpu/drm/asahi).

## Crescimento no tempo

Cada amostra inclui inventário/PSS, Niri, Quickshell, filhos, contagem de
processos e threads, CPU, memória disponível, swap, PSI e cgroups. A análise
offline reporta deltas e monotonicidade. Só marca um processo como
`POSSIBLE_LEAK_CANDIDATE` quando a mesma identidade PID + `start_time_ticks`
mantém PSS e private-dirty crescentes em todas as quatro amostras. É uma pista,
nunca diagnóstico: carga funcional, objetos retidos e cache precisam ser
investigados antes de chamar isso de vazamento.

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
