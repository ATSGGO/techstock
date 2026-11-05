-- Criação do Banco de Dados para o TechStock Manager
CREATE DATABASE IF NOT EXISTS techstock;
USE techstock;

-- 1. Tabela de Categorias
CREATE TABLE IF NOT EXISTS categorias (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nome VARCHAR(100) NOT NULL UNIQUE
);

-- 2. Tabela de Fornecedores
CREATE TABLE IF NOT EXISTS fornecedores (
    id INT AUTO_INCREMENT PRIMARY KEY,
    razao_social VARCHAR(150) NOT NULL,
    cnpj VARCHAR(20) NOT NULL UNIQUE,
    telefone VARCHAR(20) NOT NULL,
    email VARCHAR(100) NOT NULL
);

-- 3. Tabela de Produtos / Catálogo (Hardware, Software, Periféricos)
CREATE TABLE IF NOT EXISTS produtos (
    id INT AUTO_INCREMENT PRIMARY KEY,
    sku VARCHAR(50) NOT NULL UNIQUE,
    nome_comercial VARCHAR(150) NOT NULL,
    fabricante VARCHAR(100) NOT NULL,
    classificacao ENUM('Hardware', 'Software', 'Periférico') NOT NULL,
    especificacao_tecnica TEXT,
    modalidade_licenciamento VARCHAR(50) DEFAULT 'N/A',
    valor_unitario DECIMAL(10, 2) NOT NULL CHECK (valor_unitario > 0),
    volume_estoque INT NOT NULL CHECK (volume_estoque >= 0),
    ponto_reposicao INT NOT NULL CHECK (ponto_reposicao >= 0),
    localizacao VARCHAR(100) NOT NULL,
    categoria_id INT NOT NULL,
    fornecedor_id INT NOT NULL,
    FOREIGN KEY (categoria_id) REFERENCES categorias(id),
    FOREIGN KEY (fornecedor_id) REFERENCES fornecedores(id)
);

-- 4. Tabela de Registo de Movimentações (Auditoria de Fluxo)
CREATE TABLE IF NOT EXISTS movimentacoes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    tipo_operacao ENUM('Entrada', 'Saída') NOT NULL,
    quantidade INT NOT NULL CHECK (quantidade > 0),
    data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    justificativa TEXT NOT NULL,
    produto_id INT NOT NULL,
    FOREIGN KEY (produto_id) REFERENCES produtos(id)
);

-- Carga Inicial de Testes Obrigatória
INSERT INTO categorias (nome) VALUES ('Processamento'), ('Armazenamento'), ('Suítes de Escritório'), ('Sistemas Operacionais');

INSERT INTO fornecedores (razao_social, cnpj, telefone, email) VALUES 
('TechDistribuição Ltda', '12.345.678/0001-90', '(11) 98765-4321', 'contato@techdistribui.com'),
('Global Softwares Corp', '98.765.432/0001-12', '(21) 91234-5678', 'vendas@globalsoft.com'),
('Hardware Express Brasil', '45.678.901/0001-55', '(41) 99887-7665', 'suporte@hwexpress.com');

INSERT INTO produtos (sku, nome_comercial, fabricante, classificacao, especificacao_tecnica, modalidade_licenciamento, valor_unitario, volume_estoque, ponto_reposicao, localizacao, categoria_id, fornecedor_id) VALUES 
('PROC-I7-147K', 'Processador Intel Core i7-14700K', 'Intel', 'Hardware', 'LGA1700, 3.4GHz', 'N/A', 2200.00, 8, 3, 'Prateleira A1', 1, 3),
('SSD-NVME-1TB', 'SSD NVMe M.2 1TB Kingston', 'Kingston', 'Hardware', 'Leitura 3500MB/s', 'N/A', 450.00, 2, 5, 'Prateleira B2', 2, 1),
('M365-BUS-ANN', 'Microsoft 365 Business Standard', 'Microsoft', 'Software', 'Licenciamento corporativo cloud', 'Assinatura Anual', 600.00, 50, 10, 'Cofre Digital', 3, 2);