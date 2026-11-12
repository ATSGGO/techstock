PRAGMA foreign_keys = ON;
BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS categorias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS fornecedores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    razao_social TEXT NOT NULL,
    cnpj TEXT NOT NULL UNIQUE,
    telefone TEXT NOT NULL,
    email TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS produtos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE,
    nome_comercial TEXT NOT NULL,
    fabricante TEXT NOT NULL,
    classificacao TEXT NOT NULL CHECK (classificacao IN ('Hardware', 'Software', 'Periférico')),
    especificacao_tecnica TEXT,
    modalidade_licenciamento TEXT DEFAULT 'N/A',
    valor_unitario REAL NOT NULL CHECK (valor_unitario > 0),
    volume_estoque INTEGER NOT NULL CHECK (volume_estoque >= 0),
    ponto_reposicao INTEGER NOT NULL CHECK (ponto_reposicao >= 0),
    localizacao TEXT NOT NULL,
    categoria_id INTEGER,
    fornecedor_id INTEGER,
    FOREIGN KEY (categoria_id) REFERENCES categorias(id),
    FOREIGN KEY (fornecedor_id) REFERENCES fornecedores(id)
);

CREATE TABLE IF NOT EXISTS movimentacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo_operacao TEXT NOT NULL CHECK (tipo_operacao IN ('Entrada', 'Saída')),
    quantidade INTEGER NOT NULL CHECK (quantidade > 0),
    data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    justificativa TEXT NOT NULL,
    produto_id INTEGER,
    FOREIGN KEY (produto_id) REFERENCES produtos(id)
);

CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome_completo TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    senha_hash TEXT NOT NULL,
    perfil_acesso TEXT NOT NULL CHECK (perfil_acesso IN ('OPERADOR', 'GERENTE')),
    status TEXT NOT NULL DEFAULT 'ATIVO' CHECK (status IN ('ATIVO', 'INATIVO'))
);

CREATE TABLE IF NOT EXISTS vendas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    numero_pedido TEXT NOT NULL UNIQUE,
    data_hora TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cliente_nome TEXT NOT NULL,
    cliente_documento TEXT NOT NULL,
    forma_pagamento TEXT NOT NULL CHECK (
        forma_pagamento IN ('PIX', 'CARTAO_CREDITO', 'BOLETO_FATURADO')
    ),
    valor_total REAL NOT NULL CHECK (valor_total >= 0),
    status_venda TEXT NOT NULL DEFAULT 'CONCLUIDA'
        CHECK (status_venda IN ('CONCLUIDA', 'CANCELADA')),
    chave_autenticidade TEXT NOT NULL,
    usuario_id INTEGER NOT NULL,
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
);

CREATE TABLE IF NOT EXISTS itens_venda (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venda_id INTEGER NOT NULL,
    produto_id INTEGER NOT NULL,
    quantidade_vendida INTEGER NOT NULL CHECK (quantidade_vendida > 0),
    preco_unitario_praticado REAL NOT NULL CHECK (preco_unitario_praticado > 0),
    subtotal_item REAL NOT NULL CHECK (subtotal_item >= 0),
    FOREIGN KEY (venda_id) REFERENCES vendas(id),
    FOREIGN KEY (produto_id) REFERENCES produtos(id)
);

CREATE TABLE IF NOT EXISTS auditoria_precos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    produto_id INTEGER,
    sku TEXT NOT NULL,
    usuario_id INTEGER NOT NULL,
    data_hora TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    valor_anterior REAL NOT NULL CHECK (valor_anterior > 0),
    valor_novo REAL NOT NULL CHECK (valor_novo > 0),
    FOREIGN KEY (produto_id) REFERENCES produtos(id) ON DELETE SET NULL,
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_vendas_chave_autenticidade
    ON vendas (chave_autenticidade);

CREATE TRIGGER IF NOT EXISTS trg_vendas_chave_autenticidade_insert
BEFORE INSERT ON vendas
WHEN NEW.chave_autenticidade IS NULL OR trim(NEW.chave_autenticidade) = ''
BEGIN
    SELECT RAISE(ABORT, 'Chave de autenticidade obrigatória.');
END;

CREATE TRIGGER IF NOT EXISTS trg_vendas_chave_autenticidade_update
BEFORE UPDATE OF chave_autenticidade ON vendas
WHEN NEW.chave_autenticidade IS NULL OR trim(NEW.chave_autenticidade) = ''
BEGIN
    SELECT RAISE(ABORT, 'Chave de autenticidade obrigatória.');
END;

INSERT OR IGNORE INTO categorias (nome) VALUES
    ('Hardware'), ('Software'), ('Periféricos'),
    ('Processamento'), ('Armazenamento'), ('Suítes de Escritório');

INSERT OR IGNORE INTO fornecedores (razao_social, cnpj, telefone, email) VALUES
    ('TechStore Distribuição', '12.345.678/0001-90',
     '(11) 98765-4321', 'contato@techstore.com'),
    ('Global Softwares Corp', '98.765.432/0001-12',
     '(21) 91234-5678', 'vendas@globalsoft.com'),
    ('Hardware Express Brasil', '45.678.901/0001-55',
     '(41) 99887-7665', 'suporte@hwexpress.com');

INSERT OR IGNORE INTO produtos (
    sku, nome_comercial, fabricante, classificacao, especificacao_tecnica,
    modalidade_licenciamento, valor_unitario, volume_estoque, ponto_reposicao,
    localizacao, categoria_id, fornecedor_id
)
SELECT 'PROC-I7-147K', 'Processador Intel Core i7-14700K', 'Intel', 'Hardware',
       'LGA1700, 3.4GHz', 'N/A', 2200.00, 8, 3, 'Prateleira A1',
       (SELECT id FROM categorias WHERE nome = 'Hardware'),
       (SELECT id FROM fornecedores WHERE cnpj = '45.678.901/0001-55');

INSERT OR IGNORE INTO produtos (
    sku, nome_comercial, fabricante, classificacao, especificacao_tecnica,
    modalidade_licenciamento, valor_unitario, volume_estoque, ponto_reposicao,
    localizacao, categoria_id, fornecedor_id
)
SELECT 'SSD-NVME-1TB', 'SSD NVMe M.2 1TB Kingston', 'Kingston', 'Hardware',
       'Leitura 3500MB/s', 'N/A', 450.00, 2, 5, 'Prateleira B2',
       (SELECT id FROM categorias WHERE nome = 'Armazenamento'),
       (SELECT id FROM fornecedores WHERE cnpj = '12.345.678/0001-90');

INSERT OR IGNORE INTO produtos (
    sku, nome_comercial, fabricante, classificacao, especificacao_tecnica,
    modalidade_licenciamento, valor_unitario, volume_estoque, ponto_reposicao,
    localizacao, categoria_id, fornecedor_id
)
SELECT 'M365-BUS-ANN', 'Microsoft 365 Business Standard', 'Microsoft', 'Software',
       'Licenciamento corporativo cloud', 'Assinatura Anual', 600.00, 50, 10,
       'Cofre Digital',
       (SELECT id FROM categorias WHERE nome = 'Suítes de Escritório'),
       (SELECT id FROM fornecedores WHERE cnpj = '98.765.432/0001-12');

INSERT OR IGNORE INTO usuarios
    (nome_completo, email, senha_hash, perfil_acesso, status)
VALUES
    ('Operador TechStore', 'operador@techstore.com',
     'pbkdf2:sha256:1000000$t3S6n1O7abFREGsg$ca12ff5113c942f8d1faea8d0e24cad9c855e6bc3a9fbe0c9105e910e4da2ebd',
     'OPERADOR', 'ATIVO'),
    ('Gerente TechStore', 'gerente@techstore.com',
     'pbkdf2:sha256:1000000$pW53PwHKD6Kz9OMa$49822632424b7ca2132d7b8fffc870a231ba7901690f783efa96c6d254262193',
     'GERENTE', 'ATIVO');

INSERT OR IGNORE INTO vendas (
    numero_pedido, cliente_nome, cliente_documento, forma_pagamento,
    valor_total, status_venda, chave_autenticidade, usuario_id
)
SELECT 'PED-2026-SEED01', 'Cliente de teste', '000.000.000-00', 'PIX',
       450.00, 'CANCELADA', lower(hex(randomblob(32))),
       (SELECT id FROM usuarios WHERE email = 'operador@techstore.com');

INSERT OR IGNORE INTO itens_venda (
    venda_id, produto_id, quantidade_vendida, preco_unitario_praticado, subtotal_item
)
SELECT
    (SELECT id FROM vendas WHERE numero_pedido = 'PED-2026-SEED01'),
    (SELECT id FROM produtos WHERE sku = 'SSD-NVME-1TB'),
    1, 450.00, 450.00
WHERE EXISTS (SELECT 1 FROM vendas WHERE numero_pedido = 'PED-2026-SEED01')
  AND EXISTS (SELECT 1 FROM produtos WHERE sku = 'SSD-NVME-1TB')
  AND NOT EXISTS (
      SELECT 1
      FROM itens_venda
      WHERE venda_id = (SELECT id FROM vendas WHERE numero_pedido = 'PED-2026-SEED01')
        AND produto_id = (SELECT id FROM produtos WHERE sku = 'SSD-NVME-1TB')
  );

COMMIT;
