# Instalação e plugins do Niri+

Este documento descreve a arquitetura proposta no branch de release gate. Ela
não está na versão 0.1.9 de produção do M1; não use os comandos de plugin até
instalar explicitamente o candidato aprovado para o teste.

## Política de canal

`sudo niri+ install` é a única operação normal para instalar, atualizar ou
reparar o núcleo. No canal de produção ele aceita somente o repositório oficial
`LQ13ofc/asahi-system`, `origin/main` e objetos Git verificados. A atualização
normal resolve um commit imutável antes de executar código privilegiado. A
configuração de produção continua pinando Quickshell em
`55e92880d0aff75d235f283c839ec0990eaa9e17`; este candidato não altera esse pin.
O canal RC permanece explícito, exige commits fixos e não pode ser selecionado
por variável de ambiente ou por uma branch arbitrária.

O lockfile e o gitlink precisam nomear exatamente o mesmo commit do repositório
privado `LQ13ofc/quickshell-`. A autenticação Git é não interativa e executada
como o dono do checkout. Rede, autenticação, objeto ausente, origem incorreta,
divergência de lock/gitlink ou falha de materialização encerram a preparação
antes de instalar arquivos ou pacotes.

## Comandos públicos

```text
niri+ plugin list
niri+ plugin status quickshell
sudo niri+ plugin install quickshell
sudo niri+ plugin remove quickshell
sudo niri+ install [--dry-run]
```

O registro fechado atualmente contém apenas `quickshell`. Um `qs` ou uma
engine RPM presente no host, por si só, não significa que o plugin Niri+ esteja
instalado.

- Sem manifesto gerenciado, `niri+ install` não busca o repositório Quickshell.
- A primeira integração exige `sudo niri+ plugin install quickshell` e um
  núcleo Niri+ íntegro.
- Um manifesto válido em `/var/lib/niri-plus/plugins.json` faz `sudo niri+
  install` resolver e aplicar o núcleo e o pin visual como uma combinação.
- A atualização conjunta e a migração recusam começar enquanto qualquer
  processo `qs` estiver ativo ou quando o estado de processos não puder ser
  verificado. Saia da sessão gráfica e execute a atualização pelo Plasma ou
  TTY; isso evita substituir QML/RPM enquanto o shell usa esses arquivos.
- A ausência de rede/credenciais para Quickshell interrompe a atualização
  conjunta antes da transação de arquivos; não há modo automático “só núcleo”.
- `niri+ plugin remove quickshell` recusa enquanto `qs` estiver ativo. Remove
  somente runtime, unit/symlink e estado pertencentes à integração; mantém o
  RPM compartilhado, o binário independente `qs`, preferências e Plasma.
- `niri+ update` continua indisponível. Não é necessário para atualizar: use
  `sudo niri+ install`.

## Manifesto e validação de estado

O manifesto é gravado como root em `/var/lib/niri-plus/plugins.json`. O status
gerenciado só é aceito quando origem, commit, lockfile, snapshot/runtime,
versionamento do core, unit de lifecycle e entradas de ownership concordam. O
estado `legacy` é reservado para a integração 0.1.9: a migração automática
confere o checkout de runtime, hashes e lifecycle antigos antes de registrar o
plugin. Uma pasta ou `qs` não reconhecido não é tomado como propriedade do
Niri+. Um estado pinado mas inválido interrompe a atualização e exige
diagnóstico; ele não é reclassificado silenciosamente como ausente.

## Transação e pacotes

O instalador resolve e valida os objetos de ambos os repositórios antes de
alterar o host. O snapshot imutável é a única fonte de código executada como
root. Um recovery bundle é criado antes da transação de arquivos. Arquivos
gerenciados, CLI, configuração, estado e pin são verificados antes do commit;
falhas de aplicação restauram os arquivos previamente gerenciados. Não se
reutiliza um download sem validar o NEVRA esperado e a assinatura RPM.

A engine Quickshell tem NEVRA fixado no lockfile. Uma engine encontrada no RPM
DB em versão divergente só é atualizada quando o plugin está validamente
registrado e há um RPM anterior verificável no bundle offline. Uma instalação
independente divergente nunca é substituída. Os pacotes base são resolvidos
pelo DNF/Fedora e não têm NEVRA fixado nesta versão; seus NEVRAs e requisitos
RPM são anotados no recovery bundle, mas suas dependências completas não são
baixadas como um espelho offline. Se um scriptlet RPM ou uma falha abrupta
interromper a transação do DNF, a restauração dos arquivos Niri+ não implica
rollback geral de Fedora/RPM. Não há mudança de kernel, Mesa, serviços globais,
Plasma ou SDDM.

## Compatibilidade com 0.1.9

A versão 0.1.9 instalada não conhece `niri+ plugin` nem esta semântica de
`install`. Não tente `--dry-run --with-quickshell` com essa CLI. A versão
candidata só deve ser preparada pela ferramenta RC explicitamente pinada e
aprovada. Durante `niri+ install` do candidato, a integração antiga é migrada
apenas se puder ser validada integralmente; em caso contrário a migração para
antes de alterar arquivos. Preferências e arquivos do usuário ficam fora do
conjunto gerenciado.

## Estado de release

PR #22 e seu pin visual anterior são checkpoints históricos. A branch nova de
release gate adiciona esta semântica; ela não faz merge em `main`, não altera o
pin de produção e não transforma o candidato `GO` de experimento reversível em
uma aprovação de produção. Consulte [`M1_FIRST_TEST_FINAL.md`](M1_FIRST_TEST_FINAL.md)
para o procedimento final depois que o novo PR e seus hashes estiverem fixados.
