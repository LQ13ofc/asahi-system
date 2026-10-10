# Recuperação offline do Niri+

O recovery serve para restaurar somente os caminhos gerenciados pelo Niri+ a
partir de Plasma ou TTY. Ele não restaura Fedora, kernel, boot, Mesa/Asahi,
serviços globais ou o perfil inteiro do RPM. Não precisa de GitHub, internet,
credenciais, `qs` ou sessão Niri funcional.

## Preparação

```sh
sudo niri+ recovery prepare
niri+ recovery status
```

O pacote fica em `/var/lib/niri-plus/recovery`, em diretório root-owned modo
0700; archive e sidecar SHA-256 são modo 0600, root-owned e publicados de forma
atômica. Cada bundle recebe nome único, lista de paths gerenciados, versão,
commits/pins, manifesto do plugin, state de ownership, NEVRAs/requisitos RPM
consultáveis e metadados de arquivos/permissões. Os bundles anteriores são
mantidos. A publicação incompleta não substitui o ponto anterior.

O bundle cobre os arquivos Niri+ explicitamente listados no manifesto: CLI e
módulos, assets/config Niri gerenciados, units e drop-ins/symlinks do Niri+,
estado/bootstrap, manifesto Quickshell e backups internos. Configurações
pessoais externas não são removidas; arquivos `/etc/niri/*.kdl` existentes
imediatamente antes da restauração são preservados numa pasta
`preserved-edits-*` para revisão.

O RPM atualmente incluído como payload offline é apenas a engine Quickshell
anterior, caso um arquivo compatível já esteja no cache DNF e seu NEVRA possa
ser confirmado. A restauração valida a assinatura com `rpmkeys` e a transação
com `rpm --test`. Um pacote explicitamente registrado como recém-instalado pelo
Niri+ só é elegível para limpeza offline se o inventário anterior comprovar que
estava ausente; o DNF é consultado em modo cache, `--noautoremove` e confirmação
simulada antes da remoção. Se o cache não permitir provar a remoção, a
restauração dos arquivos continua e o CLI informa que esses RPMs ficaram
instalados. RPMs preexistentes/compartilhados são preservados.
O inventário registra os pacotes base explicitamente gerenciados e requisitos
quando o RPM DB os fornece, mas não contém os payloads da árvore de
dependências Fedora. Dependências transitivas podem permanecer após rollback;
uma atualização parcial de outros RPMs não é revertida por este mecanismo. A
instalação candidata exige espaço livre e aborta se não puder preparar o bundle
ou se a atualização da engine existente não tiver payload anterior verificável.

## Verificar e restaurar

`sudo niri+ install` e remoção explícita de plugin criam um novo bundle sob o
mesmo operation lock antes de mudar arquivos. `niri+ recovery status` valida o modo/owner da store, hash, nome vinculado no
sidecar, manifesto, paths whitelisted, tipos, symlinks, checksums e metadados
antes de indicar `READY`. O executável independente
`/usr/local/libexec/niri-plus-recover` contém o núcleo stdlib-only de recovery;
não importa `niri_plus.cli` nem outros módulos do produto.

De Plasma ou TTY, sem rede:

```sh
sudo /usr/local/libexec/niri-plus-recover status
sudo /usr/local/libexec/niri-plus-recover restore
```

Ou, se a CLI principal ainda funcionar:

```sh
sudo niri+ recovery restore
```

A restauração não executa scripts contidos no archive. Antes da escrita, valida
o bundle e prepara uma cópia temporária dos destinos atuais. Ela não segue
symlinks em diretórios pais e recusa caminhos não gerenciados, links externos,
hardlinks, metadados ou permissões especiais. Ao concluir, confira a sessão
Niri e use `niri+ doctor`; Plasma continua sendo o recovery gráfico.

## Limites

Esta recuperação não promete atomicidade contra corte de energia no meio de
uma transação RPM, não reinstala automaticamente dependências Fedora e não
restaura dados pessoais. Se o próprio helper standalone estiver ausente ou
corrompido, será necessário inicializar outro meio de recuperação e revisar o
bundle manualmente; o teste Cloud não valida um boot físico de emergência.
