# Protocolo de baseline e experimentos

## Coleta

`scripts/collect-performance-baseline` é um coletor read-only. Ele só lê `/proc`, `/sys`, metadados do sistema e resultados de ferramentas de consulta. Não executa `drop_caches`, não altera sysctl, serviços, pacotes, mounts ou arquivos de configuração. Sem `--output`, escreve JSON em stdout; com `--output`, grava apenas o arquivo de captura solicitado.

Cada métrica tem um estado próprio:

- `AVAILABLE`: valor coletado;
- `UNAVAILABLE`: interface/ferramenta ausente, valor inválido ou erro de leitura não relacionado a permissão;
- `PERMISSION_REQUIRED`: leitura bloqueada por permissão;
- `NOT_APPLICABLE`: interface específica não se aplica ao host (por exemplo, hardware Asahi no Cloud x86_64).

A falta de debugfs, ferramentas, arquivos de kernel e interfaces cgroup não interrompe a coleta. Bytes são usados para memória; contadores de kernel preservam seu formato documentado quando não têm unidade de bytes. RSS/PSS por processo vêm de `smaps_rollup` e podem ser parciais por permissão ou processos que terminem durante a captura. Nunca se soma RSS para estimar uso total do sistema.

## Idle comparável

1. Reinicie e faça login limpo na sessão a medir.
2. Não abra Brave, Steam ou terminal adicional. Feche aplicações que não pertençam à sessão em teste.
3. Aguarde 2–3 minutos após o login para estabilizar a sessão.
4. Execute uma coleta com `--runs 5`. O JSON preserva os cinco snapshots e calcula mediana, MAD (desvio absoluto mediano), mínimo e máximo das métricas escalares comparáveis.
5. Registre o perfil da sessão, energia/rede, horário e qualquer atividade externa em `conclusion.md`. Não compare capturas com condições diferentes como se fossem um A/B controlado.

Não use `drop_caches`: isso cria um estado artificial e muda o comportamento normal do sistema. Registre baseline e hipótese antes de uma única mudança. Faça A/B com os mesmos passos; decida `KEEP`, `REVERT` ou `INCONCLUSIVE`, documente evidências e só então faça commit e PR. Nenhuma medição Cloud x86_64 substitui medição no M1.

## Esquema e interpretação

O coletor inclui release/arquitetura/sessão/commits, memória global e por processo, cgroup v2, PSI, CPU, processos e unidades systemd, timers/sockets, XDG autostarts, serviços D-Bus, interfaces de gerenciamento de memória, mounts e I/O, rede e áudio. A ausência de um valor não equivale a zero.

`used_bytes` é `MemTotal - MemAvailable`, uma convenção do kernel para memória não disponível sem pressão; não é uma soma de RSS nem uma métrica de memória exclusivamente anônima. O CPU por processo é uma aproximação por janela de amostragem, normalizada a um núcleo. A coleta de mapas e serviços adiciona custo de leitura; use o mesmo coletor e opções em A/B.

O Cloud normalmente roda x86_64, pode não expor cgroups, systemd de usuário, debugfs, D-Bus ou PipeWire. Esses itens recebem estados parciais em vez de falhar a coleta. A captura contém nomes de processos/serviços e informações de mounts; revise o JSON antes de compartilhá-lo publicamente.
