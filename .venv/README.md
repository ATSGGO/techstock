# TechStock Manager - TechStore Informática & Serviços Corporativos

Sistema integrado de gestão de inventário e controlo de fluxo para ativos físicos (hardware/periféricos) e ativos digitais (licenciamento corporativo de software).

## 🚀 Stack Tecnológica
- **Linguagem:** Python 3.10+
- **Framework Web:** Flask
- **Base de Dados Relacional:** MySQL / MariaDB
- **Conector SQL:** PyMySQL

## 📂 Estrutura do Repositório
- `database.sql`: Schema DDL completo com restrições de integridade (`UNIQUE`, `CHECK`, `FOREIGN KEY`) e carga inicial de testes.
- `app.py`: Servidor backend em Flask responsável pelas regras de negócio, transações e consultas unificadas.
- `templates/`: Interfaces HTML estruturadas com Bootstrap (Dashboard Consolidado, Cadastro Dinâmico e Gestão de Movimentações).

## ⚙️ Instruções de Instalação e Execução

1. **Configurar a Base de Dados:**
   - Importe o conteúdo do ficheiro `database.sql` para o seu servidor MySQL. O nome da base de dados configurado é `techstock`.

2. **Instalar Dependências:**
   Abra o terminal na pasta do projeto e execute:
   ```bash
   python -m pip install flask pymysql