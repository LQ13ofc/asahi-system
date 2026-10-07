# Instruções permanentes do projeto

## Escopo e repositórios

- Este repositório, `LQ13ofc/asahi-system`, é a fonte principal da configuração do Fedora Asahi instalado no MacBook Air M1.
- `LQ13ofc/quickshell-` é a interface Quickshell/Niri e permanece um repositório separado. Não copie seus arquivos para cá, não o transforme em submódulo e não faça commits cruzados. Se uma mudança de sistema depender da interface, registre a dependência e trabalhe em cada repositório na sua própria branch e PR.
- O alvo é MacBook Air M1 2020, Apple M1, 8 GB de memória, `aarch64` e Fedora Asahi Remix 44.
- As sessões Niri Performance e Gaming são objetivos futuros. Não trate componentes ainda não implementados como configuração já existente.

## Segurança do sistema

- Preserve o Plasma instalado e inicializável como sessão de recuperação.
- Não remova ou desative Plasma, serviços ou pacotes, nem aplique otimizações, sem uma tarefa explícita que autorize aquela mudança.
- Não altere kernel, bootloader, Mesa, drivers Asahi, zswap, muvm ou FEX sem autorização explícita para esse componente e um plano de recuperação revisável.
- O Codex Cloud serve para desenvolvimento e verificações estáticas. Não execute nele ações destinadas a mudar o Mac físico e não considere testes em x86 equivalentes a testes no M1.
- Trate `hardware/mba-m1-8gb/baseline/` como evidência histórica imutável. Ao coletar dados novos, adicione uma captura identificada por data em vez de sobrescrever a anterior.
- Se um teste futuro precisar do M1, agrupe coleta e comandos em um único script revisável e forneça um único comando para executá-lo.
- Nunca versionar senhas, tokens, URLs secretas de calendário, chaves privadas ou dados pessoais coletados durante testes.

## Desenvolvimento e Git

- Desenvolva primeiro no Codex Cloud; reserve o M1 para verificações de hardware, Asahi, GPU Honeykrisp, Niri real, consumo de RAM, jogos, FEX/muvm e benchmarks.
- Mantenha `main` como estado conhecido e revisável. Faça mudanças em branches, execute as validações aplicáveis, use commits pequenos e coerentes e abra PR para revisão. Não faça mudanças diretamente em `main`.
- Prefira Python da biblioteca padrão para verificações do repositório. Declare dependências adicionais e mantenha ferramentas de teste isoladas do sistema operacional.
- Scripts de preparação devem explicar o que verificam e não alterar o sistema por padrão. Separe coleta, validação e aplicação de configuração.
- Documente limites de cada teste: validação estática não prova funcionamento no kernel Asahi, na GPU, em Wayland/Niri real ou desempenho de RAM.

