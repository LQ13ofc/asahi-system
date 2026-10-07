# Arquitetura e workflow

## Limites entre repositórios

| Repositório | Responsabilidade | Estado observado |
|---|---|---|
| [`asahi-system`](https://github.com/LQ13ofc/asahi-system) | Fonte principal do sistema Fedora Asahi: perfil do hardware, baseline e futura configuração reproduzível do sistema. | Por enquanto contém o baseline do MacBook e validação estática; ainda não declara pacotes ou sessões. |
| [`quickshell-`](https://github.com/LQ13ofc/quickshell-) | Interface da barra e painéis Quickshell usados com Niri. | Mantém QML, serviços, `niri/barra.kdl`, documentação e harness de teste no próprio repositório. |

A integração entre eles é uma dependência de runtime: o sistema deverá fornecer os programas, bibliotecas e serviços que a barra usa; a configuração e o código da barra continuam pertencendo a `quickshell-`. O README da interface documenta Quickshell 0.2.1, Niri 26.04, `qs -c barra` e dependências como NetworkManager, PipeWire, BlueZ, UPower, Python D-Bus/GObject e `curl` (`cava` é opcional). Ao alterar essa interface, atualize e revise cada repositório separadamente.

Os objetivos de sessão ainda são planejamento: Niri Performance usará Niri, Quickshell, PipeWire/WirePlumber e NetworkManager; Gaming terá uma sessão dedicada mínima com Gamescope ou alternativa e Steam/muvm/FEX quando necessário. Plasma continua instalado como sessão de recuperação.

## O que o baseline mostra

A captura em `hardware/mba-m1-8gb/baseline/` foi gerada em 6 de outubro de 2026 no horário local indicado em `generated.txt`. Ela registra Fedora Asahi Remix 44, kernel Asahi `aarch64`, Apple M1 e 8 CPUs (clusters Icestorm e Firestorm). `MemTotal` reporta cerca de 7,3 GiB visíveis ao Linux; esse valor não contradiz os 8 GB físicos, pois é a memória disponível após reservas do hardware.

O estado capturado está em uma sessão Plasma em uso, com navegador, Discover e outros processos abertos. A memória usada na captura não é uma medição de login limpo e não deve ser comparada diretamente com a meta futura de RAM da sessão Niri. `zram.txt` está vazio e `zswap.txt` registra zswap habilitado; o baseline apenas documenta esses valores, sem propor mudanças.

## Codex Cloud → GitHub → M1

1. Faça o desenvolvimento no Cloud, em branch do repositório responsável pela mudança. Mantenha código de sistema em `asahi-system` e código de interface em `quickshell-`.
2. Rode as validações estáticas disponíveis no Cloud. No repositório principal: `python3 scripts/validate_baseline.py` e `python3 -m unittest discover -s tests -v`. O workflow do GitHub Actions repete essas verificações em PRs.
3. Faça commits pequenos e abra PR. Revise o diff e os resultados do CI antes de integrar; `main` deve continuar representando uma configuração conhecida e revisável.
4. Só depois da revisão use o M1 para testes que dependem do hardware ou da sessão Asahi real. Cloud pode validar formato, scripts e lógica isolada; não comprova kernel, boot, GPU Honeykrisp, Wayland/Niri real, uso de RAM, jogos, FEX/muvm ou desempenho.
5. Se for necessário coletar informação ou executar um teste no M1, mantenha tudo em um único script versionado e entregue um único comando de execução. Registre novas capturas sem substituir o baseline anterior.

Nenhuma otimização ou alteração do sistema físico faz parte deste workflow inicial. Mudanças que afetem Plasma, serviços, pacotes ou os componentes protegidos em `AGENTS.md` precisam de autorização explícita e revisão própria.

## Ferramentas de validação

O validador do baseline e seus testes usam Python padrão, sem acesso ao M1 nem dependências externas. O harness de `quickshell-` também pode rodar fora do Wayland com stubs, mas requer PySide6; renderizar cenas também requer Pillow. Mesmo quando passa, esse harness cobre carga/renderização e estados simulados, não D-Bus, rede, compositor ou hardware real. Consulte o README e o `AGENTS.md` daquele repositório para os comandos e limitações atuais.

