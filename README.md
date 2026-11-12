# TechStock Manager

Aplicação Flask de catálogo e controle de estoque com autenticação por perfil,
ponto de venda (PDV), checkout transacional SQLite e recibos.

## Requisitos

- Python 3.10 ou superior
- Windows PowerShell (os comandos abaixo usam o ambiente virtual do projeto)

## Instalação e execução

Na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:TECHSTOCK_SECRET_KEY = "substitua-por-uma-chave-secreta-longa"
.\.venv\Scripts\python.exe app.py
```

Abra <http://127.0.0.1:5000>. Na primeira inicialização, o aplicativo cria as
tabelas que faltarem, mantém os dados existentes e inclui dados de demonstração.
O banco padrão fica em `techstock.db`. Para apontar para outro arquivo:

```powershell
$env:TECHSTOCK_DATABASE = "C:\caminho\para\techstock.db"
```

### Usar o sistema interativamente no terminal

Para usar o TechStock dentro do terminal integrado do VS Code (entrar, cadastrar
conta, consultar catálogo, montar carrinho no PDV, fechar venda e abrir recibo),
sem abrir o navegador:

```powershell
.\.venv\Scripts\python.exe app.py --terminal
```

O menu aplica os mesmos perfis: o operador pode consultar estoque, vender e ver
seus recibos; o gerente também pode cancelar vendas, movimentar estoque e
cadastrar produtos. No PDV, informe os SKUs exibidos, quantidades, dados do
cliente e forma de pagamento. Digite `F` para finalizar o carrinho ou `Q` para
cancelar. Estoque insuficiente aborta a venda inteira.

O comando antigo continua sendo aceito como atalho:

```powershell
.\.venv\Scripts\python.exe app.py --tabela
```

Para iniciar a interface web no navegador, execute sem opções:

```powershell
.\.venv\Scripts\python.exe app.py
```

O `migration_v2.sql` contém a DDL completa e uma carga de exemplo idempotente.
Ao iniciar `app.py`, o aplicativo também atualiza bancos existentes e gera
chaves de autenticidade para vendas antigas. Para aplicar manualmente o script
SQL, use somente um banco novo ou vazio; não o execute sobre um banco legado:

```powershell
.\.venv\Scripts\python.exe -c "import sqlite3; db=sqlite3.connect('techstock.db'); db.executescript(open('migration_v2.sql', encoding='utf-8').read()); db.close()"
```

Faça backup do banco antes de aplicar migrações em ambientes com dados reais.
As credenciais e produtos fornecidos são apenas para demonstração; altere as
senhas antes de disponibilizar o sistema fora de um ambiente local.

## Credenciais de demonstração

| Perfil | E-mail | Senha |
|---|---|---|
| Operador | `operador@techstore.com` | `Operador123!` |
| Gerente | `gerente@techstore.com` | `Gerente123!` |

As senhas são armazenadas com `werkzeug.security.generate_password_hash` e
validadas com `check_password_hash`. Sessões têm duração de oito horas; defina
`TECHSTOCK_SECRET_KEY` para uma chave longa e privada.

## Criar uma conta

Na tela de login, selecione **Criar conta de operador**. Informe seu nome, um
e-mail válido e uma senha de pelo menos 8 caracteres. Após criar a conta, entre
com o e-mail e senha cadastrados. O cadastro aberto cria apenas usuários com
perfil `OPERADOR`; contas `GERENTE` devem ser provisionadas pela administração.

Gerentes podem editar produtos; alterações de preço ficam registradas com o
usuário responsável. O saldo de estoque deve ser alterado pela tela de
movimentação, para manter a auditoria. Produtos com vendas ou movimentações
associadas não podem ser excluídos, preservando o histórico.

Após uma venda, o recibo é aberto diretamente e inclui uma chave de
autenticidade verificável pelo link exibido no próprio comprovante.

## Como testar

### Venda válida

1. Entre como operador e abra **Abrir PDV**.
2. Informe nome e documento do cliente, selecione a forma de pagamento.
3. Adicione um ou mais produtos e quantidades disponíveis.
4. O nome aceita até 50 caracteres; informe CPF ou CNPJ com ou sem pontuação.
   O limite é 250 unidades por produto, somando linhas repetidas no carrinho.
5. Confirme a venda. O sistema abre o recibo, que pode ser impresso e validado
   pela chave de autenticidade.
6. O estoque é debitado e cada item gera uma movimentação de saída de auditoria.

### Falta de estoque / rollback

1. No PDV, selecione **SSD NVMe M.2 1TB Kingston**, cujo estoque inicial é 2.
2. Informe quantidade 3 e confirme.
3. O checkout informa a quantidade disponível e não conclui o pedido. O cabeçalho,
   itens, movimentações e alteração de estoque não são persistidos.

### Permissões

- O operador pode consultar o catálogo, registrar vendas e abrir recibos das
  próprias vendas; as páginas de cadastro, movimentação manual e relatórios
  respondem com acesso proibido.
- O gerente pode cadastrar produtos, movimentar estoque, consultar relatórios e
  cancelar uma venda concluída. O cancelamento e o estorno do estoque/auditoria
  também são executados na mesma transação.
