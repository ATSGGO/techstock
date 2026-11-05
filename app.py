from flask import Flask, render_template, request, redirect, url_for
import sqlite3

app = Flask(__name__)

def obter_conexao():
    conexao = sqlite3.connect('techstock.db')
    conexao.row_factory = sqlite3.Row
    return conexao

def criar_tabelas():
    conexao = obter_conexao()
    cursor = conexao.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS categorias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL UNIQUE
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fornecedores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            razao_social TEXT NOT NULL,
            cnpj TEXT NOT NULL UNIQUE,
            telefone TEXT NOT NULL,
            email TEXT NOT NULL
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS produtos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT NOT NULL UNIQUE,
            nome_comercial TEXT NOT NULL,
            fabricante TEXT NOT NULL,
            classificacao TEXT NOT NULL,
            especificacao_tecnica TEXT,
            modalidade_licenciamento TEXT DEFAULT 'N/A',
            valor_unitario REAL NOT NULL,
            volume_estoque INTEGER NOT NULL,
            ponto_reposicao INTEGER NOT NULL,
            localizacao TEXT NOT NULL,
            categoria_id INTEGER,
            fornecedor_id INTEGER,
            FOREIGN KEY (categoria_id) REFERENCES categorias(id),
            FOREIGN KEY (fornecedor_id) REFERENCES fornecedores(id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS movimentacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo_operacao TEXT NOT NULL,
            quantidade INTEGER NOT NULL,
            data_hora TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            justificativa TEXT NOT NULL,
            produto_id INTEGER,
            FOREIGN KEY (produto_id) REFERENCES produtos(id)
        )
    ''')
    
    # Inserir dados iniciais de exemplo se as tabelas estiverem vazias
    cursor.execute("SELECT COUNT(*) FROM categorias")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO categorias (nome) VALUES ('Eletrónicos')")
        cursor.execute("INSERT INTO categorias (nome) VALUES ('Software')")
        cursor.execute("INSERT INTO categorias (nome) VALUES ('Hardware')")

    cursor.execute("SELECT COUNT(*) FROM fornecedores")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO fornecedores (razao_social, cnpj, telefone, email) VALUES ('Tech Distribuidor Lda', '12.345.678/0001-99', '912345678', 'geral@techdist.com')")

    conexao.commit()
    conexao.close()

# Executa a criação das tabelas ao iniciar
criar_tabelas()

# ---------------------------------------------------------
# ROTA: Dashboard (Página Inicial)
# ---------------------------------------------------------
@app.route('/')
def dashboard():
    conexao = obter_conexao()
    cursor = conexao.cursor()
    
    sql = """
        SELECT 
            p.id,
            p.sku,
            p.nome_comercial AS ativo,
            p.fabricante,
            c.nome AS categoria,
            f.razao_social AS fornecedor,
            p.valor_unitario,
            p.volume_estoque,
            p.ponto_reposicao,
            p.localizacao,
            p.modalidade_licenciamento
        FROM produtos p
        LEFT JOIN categorias c ON p.categoria_id = c.id
        LEFT JOIN fornecedores f ON p.fornecedor_id = f.id;
    """
    
    cursor.execute(sql)
    produtos = cursor.fetchall()
    
    conexao.close()
    return render_template('index.html', produtos=produtos)

# ---------------------------------------------------------
# ROTA: Atalho para Novo Ativo (Evita Erro 404)
# ---------------------------------------------------------
@app.route('/novo')
def novo():
    return redirect(url_for('cadastrar_produto'))

# ---------------------------------------------------------
# ROTA: Cadastrar Produto
# ---------------------------------------------------------
@app.route('/cadastrar', methods=['GET', 'POST'])
def cadastrar_produto():
    conexao = obter_conexao()
    cursor = conexao.cursor()

    if request.method == 'POST':
        sku = request.form.get('sku')
        nome_comercial = request.form.get('nome_comercial')
        fabricante = request.form.get('fabricante')
        classificacao = request.form.get('classificacao')
        especificacao_tecnica = request.form.get('especificacao_tecnica')
        modalidade_licenciamento = request.form.get('modalidade_licenciamento', 'N/A')
        valor_unitario = request.form.get('valor_unitario')
        volume_estoque = request.form.get('volume_estoque')
        ponto_reposicao = request.form.get('ponto_reposicao')
        localizacao = request.form.get('localizacao')
        categoria_id = request.form.get('categoria_id')
        fornecedor_id = request.form.get('fornecedor_id')

        cursor.execute('''
            INSERT INTO produtos (
                sku, nome_comercial, fabricante, classificacao, 
                especificacao_tecnica, modalidade_licenciamento, 
                valor_unitario, volume_estoque, ponto_reposicao, 
                localizacao, categoria_id, fornecedor_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            sku, nome_comercial, fabricante, classificacao, 
            especificacao_tecnica, modalidade_licenciamento, 
            valor_unitario, volume_estoque, ponto_reposicao, 
            localizacao, categoria_id, fornecedor_id
        ))
        
        conexao.commit()
        conexao.close()
        return redirect(url_for('dashboard'))

    cursor.execute("SELECT * FROM categorias")
    categorias = cursor.fetchall()

    cursor.execute("SELECT * FROM fornecedores")
    fornecedores = cursor.fetchall()

    conexao.close()
    return render_template('cadastrar.html', categorias=categorias, fornecedores=fornecedores)

# ---------------------------------------------------------
# ROTA: Movimentar Stock
# ---------------------------------------------------------
@app.route('/movimentar', methods=['GET', 'POST'])
def movimentar():
    conexao = obter_conexao()
    cursor = conexao.cursor()

    if request.method == 'POST':
        produto_id = request.form.get('produto_id')
        tipo_operacao = request.form.get('tipo_operacao')
        quantidade = int(request.form.get('quantidade'))
        justificativa = request.form.get('justificativa')

        cursor.execute('''
            INSERT INTO movimentacoes (tipo_operacao, quantidade, justificativa, produto_id)
            VALUES (?, ?, ?, ?)
        ''', (tipo_operacao, quantidade, justificativa, produto_id))

        if tipo_operacao == 'Entrada':
            cursor.execute('UPDATE produtos SET volume_estoque = volume_estoque + ? WHERE id = ?', (quantidade, produto_id))
        else:
            cursor.execute('UPDATE produtos SET volume_estoque = volume_estoque - ? WHERE id = ?', (quantidade, produto_id))

        conexao.commit()
        conexao.close()
        return redirect(url_for('dashboard'))

    cursor.execute("SELECT * FROM produtos")
    produtos = cursor.fetchall()
    conexao.close()
    return render_template('movimentar.html', produtos=produtos)

if __name__ == '__main__':
    app.run(debug=True, port=5000)