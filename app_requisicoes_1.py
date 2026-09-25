import os
import time
import warnings
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
import pandas as pd
from io import BytesIO
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from werkzeug.security import generate_password_hash, check_password_hash
import threading
from flask import jsonify




app = Flask(__name__)

# ==============================================================================
# CONFIGURAÇÕES DE SEGURANÇA E SESSÃO
# ==============================================================================
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "chave_secreta_altamente_segura_aqui_123987")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=False,  # Mude para True se utilizar HTTPS (ex: Render)
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=2)
)

warnings.filterwarnings("ignore", category=UserWarning)

# ==============================================================================
# POOL DE CONEXÕES POSTGRESQL (HOSTINGER)
# ==============================================================================
DB_HOST = "179.199.138.119"
DB_NAME = "Minhabase"
DB_USER = "ramonn"
DB_PASS = "Synk2709@VPS"
DB_PORT = "5432"

try:
    db_pool = pool.SimpleConnectionPool(
        1, 10,
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
        port=DB_PORT
    )
    print("🎯 Pool de conexões do PostgreSQL estabelecido com sucesso!")
except Exception as e:
    print(f"❌ Erro crítico ao criar Pool de conexões: {e}")
    db_pool = None

class GerenciadorConexao:
    """Context Manager para obter e devolver conexões ao pool automaticamente."""
    def __init__(self):
        self.conn = None
    def __enter__(self):
        if not db_pool:
            raise Exception("Pool de conexões com o banco de dados não está disponível.")
        self.conn = db_pool.getconn()
        return self.conn
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            db_pool.putconn(self.conn)

# Dicionário global para monitorar o status do upload em tempo real
status_importacao = {"progresso": 0, "status": "Aguardando arquivo..."}

def executar_importacao_segundo_plano(df, usr_dono, perfil_ativo, limite_permitido):
    """Executa o processamento do Excel em segundo plano (Thread) com progresso real de gravação."""
    global status_importacao
    
    try:
        total_linhas = len(df)
        status_importacao["status"] = "Analisando estrutura do catálogo..."
        
        lista_dados_pai = []
        lista_concorrentes = []
        itens_cadastrados = 0
        itens_ignorados = 0
        
        # 1. LEITURA RÁPIDA E VALIDAÇÃO DOS DADOS EM MEMÓRIA
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                for index, row in df.iterrows():
                    cod_prod = str(row.get('codigo_produto_base', '')).strip()
                    if not cod_prod or cod_prod == 'nan':
                        continue
                        
                    cidade = str(row.get('cidade', 'Não Informada')).strip()
                    valor_flag_bruto = str(row.get('importar_concorrentes', '')).strip().lower()
                    flag_concorrente = "" if valor_flag_bruto == "nan" else valor_flag_bruto
                    
                    try:
                        dias = int(row.get('dias_frequencia', 1))
                    except:
                        dias = 1
                        
                    # Consulta se o produto existe no catálogo base
                    cur.execute("""
                        SELECT ean_principal, ean_concorrente_1, ean_concorrente_2, ean_concorrente_3
                        FROM produtos WHERE LOWER(codigo_produto) = %s
                    """, (cod_prod.lower(),))
                    prod_dados = cur.fetchone()
                    
                    if not prod_dados or not prod_dados[0] or str(prod_dados[0]).strip() == "-":
                        itens_ignorados += 1
                        continue
                        
                    ean_principal = str(prod_dados[0]).strip()
                    
                    # Organiza os dados para inserção posterior
                    lista_dados_pai.append({
                        "ean": ean_principal,
                        "cidade": cidade,
                        "dias": dias,
                        "prod_dados": prod_dados,
                        "flag": flag_concorrente
                    })
                    
                    status_importacao["progresso"] = int(((index + 1) / total_linhas) * 20)

                # 2. SALVAMENTO SEQUENCIAL NO BANCO (Progresso de 20% a 95%)
                total_pais = len(lista_dados_pai)
                
                if total_pais == 0:
                    status_importacao["progresso"] = 100
                    status_importacao["status"] = f"Concluído! Nenhum código válido encontrado. ({itens_ignorados} ignorados)."
                    return

                # 🎯 ALTERADO: proxima_pesquisa vira CURRENT_TIMESTAMP e status vira 'Pendente' para rodar na hora
                query_pai = """
                    INSERT INTO dados (codigo_barras, cidade, proxima_pesquisa, dias, data_cadastro, usuario_dono, status)
                    VALUES (%s, %s, CURRENT_TIMESTAMP, %s, CURRENT_TIMESTAMP, %s, 'Pendente') RETURNING id
                """

                for idx, item in enumerate(lista_dados_pai):
                    status_importacao["status"] = f"Gravando agendamento {idx + 1} de {total_pais} no banco..."
                    
                    cur.execute(query_pai, (item["ean"], item["cidade"], item["dias"], usr_dono))
                    retorno_id = cur.fetchone()
                    novo_id = retorno_id[0] if retorno_id else None
                    
                    if not novo_id:
                        continue
                        
                    itens_cadastrados += 1
                    
                    concorrentes_lista = []
                    if item["flag"] == "sim":
                        for c_idx in range(1, 4):
                            if len(item["prod_dados"]) > c_idx and item["prod_dados"][c_idx]:
                                valor_concorrente = str(item["prod_dados"][c_idx]).strip()
                                if valor_concorrente and valor_concorrente != "-":
                                    concorrentes_lista.append(valor_concorrente)
                    
                    if concorrentes_lista:
                        for ean in concorrentes_lista:
                            lista_concorrentes.append((novo_id, ean))
                    else:
                        lista_concorrentes.append((novo_id, 'SEM_CONCORRENTES'))
                    
                    status_importacao["progresso"] = 20 + int(((idx + 1) / total_pais) * 75)
                
                if lista_concorrentes:
                    status_importacao["status"] = "Finalizando vínculos de concorrentes in lote..."
                    cur.executemany("INSERT INTO dados_concorrentes (dados_id, ean_concorrente) VALUES (%s, %s)", lista_concorrentes)
                
            conn.commit()
            
        status_importacao["progresso"] = 100
        msg_final = f"Concluído! {itens_cadastrados} agendamentos importados com sucesso."
        if itens_ignorados > 0:
            msg_final += f" ({itens_ignorados} códigos ignorados)."
        status_importacao["status"] = msg_final
        
    except Exception as e:
        print(f"❌ Erro na Thread de Importação: {e}")
        status_importacao["status"] = f"Erro crítico: {str(e)}"


def inicializar_banco_do_zero():
    """Garante a integridade física das tabelas no banco PostgreSQL ao subir o app."""
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                # 1. Cidades
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS cidades_sistema (
                        id SERIAL PRIMARY KEY,
                        cidade VARCHAR(100) NOT NULL UNIQUE
                    );
                """)
                cur.execute("SELECT COUNT(*) FROM cidades_sistema;")
                if cur.fetchone()[0] == 0:
                    cidades_padrao = [("Salvador",), ("Feira de Santana",), ("Porto Seguro",)]
                    cur.executemany("INSERT INTO cidades_sistema (cidade) VALUES (%s);", cidades_padrao)

                # 2. Usuários
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS usuario (
                        id SERIAL PRIMARY KEY,
                        nome VARCHAR(100) NOT NULL UNIQUE,
                        senha VARCHAR(255) NOT NULL,
                        limite_maximo INTEGER DEFAULT 100,
                        primeiro_acesso INTEGER DEFAULT 1,
                        ativo INTEGER DEFAULT 1,
                        perfil VARCHAR(20) DEFAULT 'USER'
                    );
                """)
                cur.execute("SELECT COUNT(*) FROM usuario WHERE LOWER(nome) = 'admin';")
                if cur.fetchone()[0] == 0:
                    senha_admin = generate_password_hash("admin123")
                    cur.execute("""
                        INSERT INTO usuario (nome, senha, limite_maximo, primeiro_acesso, ativo, perfil)
                        VALUES ('admin', %s, 9999, 0, 1, 'ADMIN');
                    """, (senha_admin,))

                # 3. Dados (Pai)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS dados (
                        id SERIAL PRIMARY KEY,
                        codigo_barras VARCHAR(20) NOT NULL,
                        cidade VARCHAR(100),
                        status VARCHAR(30) DEFAULT 'Pendente',
                        proxima_pesquisa DATE,
                        dias INTEGER,
                        data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        usuario_dono VARCHAR(100)
                    );
                """)

                # 4. Concorrentes (Filhos)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS dados_concorrentes (
                        id SERIAL PRIMARY KEY,
                        dados_id INTEGER REFERENCES dados(id) ON DELETE CASCADE,
                        ean_concorrente VARCHAR(20) NOT NULL
                    );
                """)
            conn.commit()
            print("🚀 Tabelas verificadas/sincronizadas com êxito total.")
    except Exception as e:
        print(f"⚠️ Falha na rotina inicializadora de tabelas: {e}")

# Executa a verificação na inicialização do script
inicializar_banco_do_zero()


def ean_valido(ean):
    return len(str(ean).strip()) > 0

def buscar_cidades_banco():
    cidades = []
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT cidade FROM cidades_sistema ORDER BY cidade ASC')
                cidades = [str(row[0]).strip() for row in cur.fetchall() if row and row[0]]
    except Exception as e:
        print(f"[LOG] Erro ao carregar cidades do banco: {e}")
        cidades = ["Salvador (Reserva)", "Feira de Santana (Reserva)", "Porto Seguro (Reserva)"]
    return cidades


@app.route("/", methods=['GET', 'POST'])
def index():
    # 1. PROCESSA A TENTATIVA DE LOGIN INTEGRADA À SUA TABELA 'usuario'
    if request.method == 'POST' and "usuario_logado" not in session:
        usuario_input = request.form.get("usuario", "").strip()
        senha_input = request.form.get("senha", "").strip()
        
        try:
            from werkzeug.security import check_password_hash
            
            with GerenciadorConexao() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id, nome, senha, perfil FROM usuario WHERE LOWER(nome) = %s", (usuario_input.lower(),))
                    usuario_banco = cur.fetchone()
                    
                    if usuario_banco:
                        id_usuario_db = usuario_banco[0]
                        nome_usuario_db = usuario_banco[1]
                        senha_db = str(usuario_banco[2]).strip()
                        perfil_db = str(usuario_banco[3]).strip().upper() if usuario_banco[3] else "USER"
                        
                        eh_valido = False
                        if senha_db.startswith("scrypt:"):
                            eh_valido = check_password_hash(senha_db, senha_input)
                        else:
                            eh_valido = (senha_input == senha_db)
                        
                        if eh_valido:
                            session.clear()
                            session["usuario_logado"] = nome_usuario_db
                            session["usuario_id"] = id_usuario_db
                            session["perfil_logado"] = perfil_db
                            
                            return redirect(url_for("index"))
            
        except Exception as err_login:
            print(f"❌ Erro crítico ao validar credenciais no banco: {err_login}")
            from flask import flash
            flash("Falha interna ao conectar na base de usuários.", "danger")
            return render_template("index.html", tela="login")



        # ==========================================================================
    # 2. BLOQUEIO DE SEGURANÇA: Se não enviou formulário e não está logado, exibe a tela de login
    # ==========================================================================
    if "usuario_logado" not in session:
        # 🎯 CORREÇÃO DEFINITIVA: Renderiza o login diretamente em vez de redirecionar e causar loop
        return render_template("index.html", tela="login")

    tela = request.args.get("tela", "painel")
    perfil_ativo = session.get("perfil_logado", "USER").upper()
    
    # 🎯 DECLARAÇÃO E POPULAÇÃO GLOBAL (Roda para qualquer tela: painel, produtos, etc.)
    categorias_opcoes = []
    usuarios_lista_filtros = []
    
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                # 1. Alimenta as categorias globais para aparecerem tanto no painel quanto nos produtos
                cur.execute("SELECT DISTINCT categoria FROM produtos WHERE ativo = 1 AND categoria IS NOT NULL AND categoria != '' ORDER BY categoria ASC")
                categorias_opcoes = [str(r[0]).strip() for r in cur.fetchall() if r and r[0]]
                
                # 2. Alimenta os operadores globais se o usuário logado for ADMIN
                if perfil_ativo == 'ADMIN':
                    cur.execute("SELECT nome FROM usuario WHERE ativo = 1 ORDER BY nome ASC")
                    usuarios_lista_filtros = [str(r[0]).strip() for r in cur.fetchall() if r and r[0]]
    except Exception as e_setup:
        print(f"⚠️ Erro ao carregar filtros globais de cabeçalho: {e_setup}")
    # ==========================================================================
    # 3. CAPTURA A TELA ATUAL ENVIADA PELO CLIQUE DO MENU
    # ==========================================================================
    tela = request.args.get("tela", "painel").strip().lower()

    # Variáveis globais de contexto iniciadas vazias
    metrics = {"total": 0, "pendentes": 0, "andamento": 0, "concluidas": 0}
    tarefas = []
    produtos_lista = []
    usuarios_lista = []
    usuarios_lista_filtros = []
    cidades_opcoes = ["Itamaraju", "Itabela", "Eunápolis", "Teixeira de Freitas"]

    # Carrega as cidades para os selects dos filtros e modais
        # Carrega as cidades para os selects dos filtros e modais
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT cidade FROM cidades_sistema ORDER BY cidade ASC")
                rows_cidades = cur.fetchall()
                if rows_cidades:
                    cidades_opcoes = [str(r[0]).strip() for r in rows_cidades if r and r[0] is not None]
                
                # 🎯 ADICIONE ESTAS LINHAS EXATAMENTE AQUI DENTRO:
                perfil_ativo_global = session.get("perfil_logado", "USER")
                if perfil_ativo_global == 'ADMIN':
                    cur.execute("SELECT nome FROM usuario WHERE ativo = 1 ORDER BY nome ASC")
                    usuarios_lista_filtros = [str(r[0]).strip() for r in cur.fetchall() if r and r[0]]

    except Exception as err_cidades:
        print(f"❌ Erro ao buscar cidades_sistema no banco: {err_cidades}")

        # ==========================================================================
    # LÓGICA DA TELA 1: REQUISIÇÕES (PAINEL) - REVISADA, BLINDADA E LIMPA
    # ==========================================================================
    if tela == "painel":
        busca = request.args.get("busca", "").strip()
        cidade_filtro = request.args.get("cidade_filtro", "").strip()
        status_filtro = request.args.get("status_filtro", "").strip().lower()
        usuario_filtro = request.args.get("usuario_filtro", "").strip().lower()

        try:
            usuario_ativo = session.get("usuario_logado")
            perfil_ativo = session.get("perfil_logado", "USER") 
            
            with GerenciadorConexao() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    sql_base = """
                        SELECT d.id, d.codigo_barras, d.cidade, d.status, d.data_cadastro, d.dias, 
                               p.codigo_produto, p.descricao_item, d.usuario_dono, d.proxima_pesquisa 
                        FROM dados d
                        LEFT JOIN produtos p ON p.ean_principal = d.codigo_barras 
                                            AND LOWER(p.usuario_dono) = LOWER(d.usuario_dono)
                        WHERE (%s = 'admin') 
                           OR (LOWER(d.usuario_dono) = %s)
                        ORDER BY d.id DESC
                    """
                    
                    cur.execute(sql_base, (usuario_ativo.lower() if usuario_ativo else '', usuario_ativo.lower() if usuario_ativo else ''))
                    rows = cur.fetchall()
                    
                    for row in rows:
                        id_real = int(row["id"])
                        ean_real = row["codigo_barras"]
                        cidade_real = row["cidade"]
                        status_real = str(row["status"]).strip()
                        data_real = row["data_cadastro"].strftime("%d/%m/%Y %H:%M") if hasattr(row["data_cadastro"], "strftime") else str(row["data_cadastro"])
                        dias_real = row["dias"] if row["dias"] is not None else 1
                        
                        cod_produto_real = row["codigo_produto"] if row["codigo_produto"] else "-"
                        desc_produto_real = row["descricao_item"] if row["descricao_item"] else "Produto Não Cadastrado"
                        usuario_dono_registro = str(row["usuario_dono"]).strip().lower() if row["usuario_dono"] else ""
                        data_proxima_pesquisa = row["proxima_pesquisa"]

                        # Consulta os concorrentes da tarefa ativa utilizando o pool estável
                        with conn.cursor() as cur_conc:
                            cur_conc.execute("SELECT ean_concorrente FROM dados_concorrentes WHERE dados_id = %s", (id_real,))
                            rows_conc = cur_conc.fetchall()
                        
                        concorrentes_lista = []
                        possui_bloqueio = False
                        
                        if rows_conc:
                            for r in rows_conc:
                                val_c = str(r[0]).strip() if r and r[0] else ""
                                if val_c == "SEM_CONCORRENTES":
                                    possui_bloqueio = True
                                elif val_c and val_c != "-":
                                    concorrentes_lista.append(val_c)
                        
                        if not concorrentes_lista and not possui_bloqueio and cod_produto_real != "-":
                            with conn.cursor() as cur_prod:
                                cur_prod.execute("""
                                    SELECT ean_concorrente_1, ean_concorrente_2, ean_concorrente_3 
                                    FROM produtos 
                                    WHERE LOWER(codigo_produto) = %s AND LOWER(usuario_dono) = %s
                                """, (str(cod_produto_real).strip().lower(), usuario_dono_registro))
                                prod_base = cur_prod.fetchone()
                            if prod_base:
                                for val in prod_base:
                                    if val and str(val).strip() != "-" and str(val).strip() != str(ean_real).strip():
                                        concorrentes_lista.append(str(val).strip())

                        concorrentes_real = ", ".join(concorrentes_lista) if concorrentes_lista else "Nenhum"

                        # Formatação amigável da data para o HTML evitar erros de renderização
                                                # Formatação amigável da data futura para a subtela
                        data_futura_txt = data_proxima_pesquisa.strftime("%d/%m/%Y") if hasattr(data_proxima_pesquisa, "strftime") else str(data_proxima_pesquisa) if data_proxima_pesquisa else "-"

                        # Montagem estrita respeitando a ordem original do seu sistema:
                        item_tarefa = [
                            id_real,              # t[0] -> ID
                            ean_real,             # t[1] -> EAN
                            concorrentes_real,    # t[2] -> Concorrentes
                            cidade_real,          # t[3] -> Cidade
                            status_real,          # t[4] -> Status
                            data_real,            # t[5] -> Data Cadastro
                            dias_real,            # t[6] -> Frequência/Volume
                            cod_produto_real,     # t[7] -> Código Produto
                            desc_produto_real,    # t[8] -> Descrição do Item
                            data_futura_txt       # t[9] -> Data da Próxima Pesquisa
                        ]

                        
                        # FILTRAGEM DINÂMICA EM MEMÓRIA
                        corresponde = True
                        
                        # LOCK DE SEGURANÇA MULTITENANCY
                        if perfil_ativo != 'ADMIN':
                            if usuario_ativo.lower() != usuario_dono_registro:
                                corresponde = False
                        else:
                            if usuario_filtro and usuario_filtro != "todos" and usuario_filtro != "":
                                if usuario_filtro != usuario_dono_registro:
                                    corresponde = False

                        if busca:
                            termo_busca = str(busca).strip().lower()
                            val_ean = str(ean_real).strip().lower()
                            val_codigo = str(cod_produto_real).strip().lower()
                            val_desc = str(desc_produto_real).strip().lower()
                            
                            if termo_busca != val_ean and termo_busca != val_codigo and termo_busca not in val_desc:
                                corresponde = False

                        # Filtro de Cidade
                        if cidade_filtro and str(cidade_real).strip() != cidade_filtro:
                            corresponde = False

                        # Filtro por Status da Requisição
                        status_limpo = status_real.lower()
                        hoje_verificacao = datetime.now().date()
                        data_pesquisa_pura = data_proxima_pesquisa.date() if hasattr(data_proxima_pesquisa, "date") else data_proxima_pesquisa

                        if status_filtro:
                            if status_filtro == 'agendados_futuros':
                                if not (("concluid" in status_limpo or "processado" in status_limpo) and data_pesquisa_pura and data_pesquisa_pura > hoje_verificacao):
                                    corresponde = False
                            elif status_filtro == 'concluida':
                                if not ("concluid" in status_limpo or "processado" in status_limpo) or (data_pesquisa_pura and data_pesquisa_pura > hoje_verificacao):
                                    corresponde = False
                            elif status_filtro == 'andamento' and 'andamento' not in status_limpo:
                                corresponde = False
                            elif status_filtro == 'erro' and 'erro' not in status_limpo:
                                corresponde = False
                            elif status_filtro == 'pendente' and ('concluid' in status_limpo or 'processado' in status_limpo or 'andamento' in status_limpo or 'erro' in status_limpo):
                                corresponde = False

                        # Se passar em todos os filtros, adiciona na lista e soma nas métricas
                        if corresponde:
                            tarefas.append(item_tarefa)
                            metrics["total"] += 1
                            
                            if "concluid" in status_limpo or "processado" in status_limpo:
                                if data_pesquisa_pura and data_pesquisa_pura > hoje_verificacao:
                                    if "agendados" not in metrics:
                                        metrics["agendados"] = 0
                                    metrics["agendados"] += 1
                                else:
                                    metrics["concluidas"] += 1
                            elif "andamento" in status_limpo:
                                metrics["andamento"] += 1
                            else:
                                metrics["pendentes"] += 1
                            
        except Exception as e:
            print(f"❌ Erro crítico na leitura filtrada da tabela dados: {e}")





        # ==========================================================================
    # LÓGICA DA TELA 2: CADASTRO DE PRODUTOS REVISADA (COM PREÇO INDEX E TRAVA DE USUÁRIO)
    # ==========================================================================
    elif tela == "produtos":
        busca_codigo = request.args.get("busca_codigo", "").strip().lower()
        busca_ean = request.args.get("busca_ean", "").strip().lower()
        busca_desc = request.args.get("busca_desc", "").strip().lower()
        # 🎯 CORREÇÃO 1: Captura o valor do filtro selecionado na caixinha WEB
        categoria_filtro = request.args.get("categoria_filtro", "").strip().lower()
        
        usuario_filtro = request.args.get("usuario_filtro", "").strip().lower()
        usuario_ativo = session.get("usuario_logado", "admin")
        perfil_ativo = session.get("perfil_logado", "USER")
        
        usuarios_lista_filtros = []
        categorias_opcoes = [] # 🎯 CORREÇÃO 2: Cria a lista para alimentar o seletor HTML

        try:
            with GerenciadorConexao() as conn:
                with conn.cursor() as cur:
                    
                    # 🎯 CORREÇÃO 3: Busca todas as categorias únicas e ativas para preencher a caixinha de filtros
                    cur.execute("SELECT DISTINCT categoria FROM produtos WHERE ativo = 1 AND categoria IS NOT NULL AND categoria != '' ORDER BY categoria ASC")
                    categorias_opcoes = [str(r[0]).strip() for r in cur.fetchall() if r and r[0]]
                    
                    if perfil_ativo == 'ADMIN':
                        cur.execute("SELECT nome FROM usuario WHERE ativo = 1 ORDER BY nome ASC")
                        usuarios_lista_filtros = [str(r[0]).strip() for r in cur.fetchall() if r and r[0]]
                        
                        # 💡 ADICIONADO: 'categoria' no final do SELECT
                        sql_produtos = """
                            SELECT id, codigo_produto, descricao_item, preco_tabela, preco_minimo, preco_index, 
                                   ean_concorrente_1, ean_concorrente_2, ean_concorrente_3, ean_principal, usuario_dono, categoria 
                            FROM produtos 
                            WHERE ativo = 1
                            ORDER BY id DESC
                        """
                        cur.execute(sql_produtos)
                    else:
                        # 💡 ADICIONADO: 'categoria' no final do SELECT
                        sql_produtos = """
                            SELECT id, codigo_produto, descricao_item, preco_tabela, preco_minimo, preco_index, 
                                ean_concorrente_1, ean_concorrente_2, ean_concorrente_3, ean_principal, usuario_dono, categoria 
                            FROM produtos 
                            WHERE LOWER(usuario_dono) = %s AND ativo = 1
                            ORDER BY id DESC
                        """
                        cur.execute(sql_produtos, (usuario_ativo.lower(),))
                        
                    rows_produtos = cur.fetchall()
                    
                    for r in rows_produtos:
                        id_p = r[0]
                        cod_p = str(r[1]).strip() if r[1] is not None else ""
                        desc_p = str(r[2]).strip() if r[2] is not None else ""
                        
                        p_tabela = float(r[3]) if r[3] is not None else 0.0
                        p_minimo = float(r[4]) if r[4] is not None else 0.0
                        preco_index = float(r[5]) if r[5] is not None else 0.0
                        
                        c1 = str(r[6]).strip() if r[6] else "-"
                        c2 = str(r[7]).strip() if r[7] else "-"
                        c3 = str(r[8]).strip() if r[8] else "-"
                        ean_p = str(r[9]).strip() if r[9] else "-"
                        dono_p = str(r[10]).strip() if len(r) > 10 and r[10] else "admin"
                        # 💡 ADICIONADO: Captura o valor da categoria de forma segura (índice 11)
                        categoria_p = str(r[11]).strip() if len(r) > 11 and r[11] else "-"

                        corresponde = True
                        if busca_codigo and busca_codigo not in cod_p.lower():
                            corresponde = False
                        if busca_ean and busca_ean not in ean_p.lower():
                            corresponde = False
                        if busca_desc and busca_desc not in desc_p.lower():
                            corresponde = False
                        
                        # 🎯 CORREÇÃO 4: Filtra a lista com base na categoria selecionada na caixinha superior
                        if categoria_filtro and categoria_filtro != "" and categoria_filtro != categoria_p.lower():
                            corresponde = False
                            
                        if perfil_ativo == 'ADMIN' and usuario_filtro and dono_p.lower() != usuario_filtro:
                            corresponde = False

                        if corresponde:
                            # 🎯 ADICIONADO: categoria_p incluído como o último elemento da lista
                            produtos_lista.append([
                                id_p, cod_p, desc_p, p_tabela, p_minimo, c1, c2, c3, ean_p, dono_p, preco_index, categoria_p
                            ])

        except Exception as err_prod:
            print(f"❌ Erro crítico ao buscar produtos cadastrados na base: {err_prod}")


       # ==========================================================================
    # LÓGICA DA TELA 3: GERENCIAMENTO DE OPERADORES E USUÁRIOS (SINCRO HTML)
    # ==========================================================================
    elif tela == "usuarios":
        if session.get("perfil_logado", "USER") != "ADMIN":
            return redirect(url_for("index", tela="painel"))
            
        try:
            with GerenciadorConexao() as conn:
                with conn.cursor() as cur:
                    # 🔍 Extrai a coluna email do banco de dados física
                    sql_usuarios = """
                        SELECT 
                            u.id, u.nome, u.email, u.perfil, u.limite_maximo, u.ativo,
                            COUNT(d.id) as qtd_cadastradas,
                            SUM(CASE WHEN LOWER(d.status) LIKE '%%conclui%%' OR LOWER(d.status) LIKE '%%processado%%' THEN 1 ELSE 0 END) as qtd_realizadas
                        FROM usuario u
                        LEFT JOIN dados d ON LOWER(d.usuario_dono) = LOWER(u.nome)
                        GROUP BY u.id, u.nome, u.email, u.perfil, u.limite_maximo, u.ativo
                        ORDER BY u.nome ASC
                    """
                    cur.execute(sql_usuarios)
                    rows_usuarios = cur.fetchall()
                    
                    for r in rows_usuarios:
                        id_user = r[0]
                        nome_user = r[1]
                        email_user = str(r[2]).strip() if r[2] else "Não Informado"
                        perfil_user = str(r[3]).strip().upper() if r[3] else "USER"
                        limite_max = int(r[4]) if r[4] and int(r[4]) > 0 else 100
                        status_ativo = int(r[5]) if r[5] is not None else 1
                        cadastradas = int(r[6]) if r[6] else 0
                        realizadas = int(r[7]) if r[7] else 0
                        
                        pct = (cadastradas / limite_max) * 100 if limite_max > 0 else 0
                        
                        # 🎯 PADRONIZAÇÃO COMPLETA COM O SEU INDEX.HTML ORIGINAL
                        # Modifique o bloco usuarios_lista.append dentro da sua função index() para ficar exatamente assim:
                                                # 🎯 PADRONIZAÇÃO FINAL CORRIGIDA COM AS SUAS VARIÁVEIS REAIS
                        usuarios_lista.append({
                            "id": id_user,
                            "nome": nome_user,
                            "email": email_user,  # Exigido pelo seu HTML para a coluna de e-mail
                            "perfil": perfil_user,
                            "limite": limite_max,
                            "ativo": status_ativo, # 🎯 CORRIGIDO: Nome exato da sua variável do app.py
                            "qtd_requisicoes": cadastradas,
                            "realizadas": realizadas,
                            "porcentagem": f"{pct:.1f}%"
                        })


                        
        except Exception as err_user:
            print(f"❌ Erro crítico ao buscar lista de usuários no index: {err_user}")



    # 📊 CAPTURA DA QUANTIDADE DE ITENS (Colado aqui com 4 espaços de recuo)
    qtd_itens_cadastrados = len(produtos_lista)

    # 4. RENDERIZAÇÃO UNIFICADA E RETORNO SEGURO DO TEMPLATE FLASK
    return render_template(
        "index.html",
        tela=tela,
        metrics=metrics,
        tarefas=tarefas,
        produtos_lista=produtos_lista,
        usuarios_lista=usuarios_lista,
        usuarios_lista_filtros=usuarios_lista_filtros,
        cidades_opcoes=cidades_opcoes,
        categorias_opcoes=categorias_opcoes,  # 🎯 INJETE ESTA LINHA EXATAMENTE AQUI DENTRO do seu return do render_template
        qtd_itens_cadastrados=qtd_itens_cadastrados
    )

    
@app.route("/cadastrar", methods=["POST"])
def cadastrar_requisicao_unica():
    if "usuario_logado" not in session:
        return redirect(url_for('index'))
        
    usr_dono = session["usuario_logado"]
    perfil_logado = session.get("perfil_logado", "USER").upper() # 🔐 Captura o perfil logado
    cb = request.form.get("codigo_barras", "").strip()
    cid = request.form.get("cidade", "Não Informada").strip()
    dias_input = request.form.get("dias_pesquisa", "1")
    
    if not cb:
        flash("O Código de Barras (EAN) principal é obrigatório!", "danger")
        return redirect(url_for("index"))
        
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                # MONITORAMENTO E ALERTA OPERACIONAL DE LIMITE MÁXIMO
                cur.execute("SELECT limite_maximo FROM usuario WHERE LOWER(nome) = %s", (usr_dono.lower(),))
                res_usuario = cur.fetchone()
                limite_permitido = res_usuario[0] if res_usuario else 100
                
                if perfil_logado != "ADMIN":
                    cur.execute("SELECT COUNT(*) FROM dados WHERE LOWER(usuario_dono) = %s", (usr_dono.lower(),))
                    qtd_cadastrada = cur.fetchone()[0]
                    
                    if (qtd_cadastrada + 1) > limite_permitido:
                        flash(f"Ação Negada! Você atingiu o limite operacional máximo de {limite_permitido} requisições.", "danger")
                        return redirect(url_for("index", tela="painel"))

                try:
                    dias = int(dias_input) if dias_input.strip() and dias_input.isdigit() else 1
                except:
                    dias = 1

                                # Processa os concorrentes vindos do formulário dinâmico
                eans_brutos = request.form.getlist("eans_concorrentes[]")
                eans_lista = []
                for ean_item in eans_brutos:
                    if "," in ean_item:
                        eans_lista.extend([e.strip() for e in ean_item.split(",") if e.strip()])
                    else:
                        if ean_item.strip():
                            eans_lista.append(ean_item.strip())

                # 🛡️ PASSO 1: Descobre quem é o dono real do produto na base física (catálogo)
                cur.execute("SELECT usuario_dono FROM produtos WHERE (ean_principal = %s OR LOWER(codigo_produto) = %s) AND ativo = 1 LIMIT 1", (cb, cb.lower()))
                res_dono_base = cur.fetchone()
                dono_real_produto = str(res_dono_base[0]).strip() if res_dono_base and res_dono_base[0] else usr_dono

                # 🛡️ PASSO 2: NOVA REGRA INVERTIDA (Dá prioridade total à escolha manual do ADMIN)
                if perfil_logado == 'ADMIN':
                    # Captura qual operador o administrador selecionou na caixinha azul do modal
                    dono_selecionado_manual = request.form.get("usuario_dono_manual", "").strip()
                    
                    if dono_selecionado_manual:
                        usuario_dono_final = dono_selecionado_manual  # 🎯 PRIORIDADE 1: O que o ADMIN escolheu na tela!
                    else:
                        usuario_dono_final = dono_real_produto        # PRIORIDADE 2: Cai para o dono do catálogo se o ADMIN deixar em branco
                else:
                    usuario_dono_final = usr_dono  # USER comum continua 100% preso e isolado na própria conta

                # query_pai estruturada e blindada gravando o usuario_dono_final correto
                query_pai = """
                    INSERT INTO dados (codigo_barras, cidade, proxima_pesquisa, dias, data_cadastro, usuario_dono, status)
                    VALUES (%s, %s, CURRENT_TIMESTAMP, %s, CURRENT_TIMESTAMP, %s, 'Pendente') RETURNING id
                """
                cur.execute(query_pai, (cb, cid, dias, usuario_dono_final))

                
                retorno_id = cur.fetchone()
                id_puro = retorno_id[0] if retorno_id else None
                
                if id_puro:
                    if not eans_lista:
                        # 🛡️ PASSO 3: Se não vier concorrente manual, busca os cadastrados no catálogo daquele produto específico
                        if perfil_logado == 'ADMIN':
                            cur.execute("""
                                SELECT ean_concorrente_1, ean_concorrente_2, ean_concorrente_3 
                                FROM produtos 
                                WHERE (ean_principal = %s OR LOWER(codigo_produto) = %s) AND ativo = 1
                            """, (cb, cb.lower()))
                        else:
                            cur.execute("""
                                SELECT ean_concorrente_1, ean_concorrente_2, ean_concorrente_3 
                                FROM produtos 
                                WHERE (ean_principal = %s OR LOWER(codigo_produto) = %s) AND LOWER(usuario_dono) = %s AND ativo = 1
                            """, (cb, cb.lower(), usr_dono.lower()))
                            
                        prod_base = cur.fetchone()
                        if prod_base:
                            for val in prod_base:
                                if val and str(val).strip() != "-" and str(val).strip() != str(cb).strip():
                                    eans_lista.append(str(val).strip())

                    if eans_lista:
                        cur.executemany(
                            "INSERT INTO dados_concorrentes (dados_id, ean_concorrente) VALUES (%s, %s)",
                            [(id_puro, str(e).strip()) for e in eans_lista if e.strip()]
                        )
                    else:
                        cur.execute("INSERT INTO dados_concorrentes (dados_id, ean_concorrente) VALUES (%s, 'SEM_CONCORRENTES')", (id_puro,))
                    
            conn.commit()
        flash("Nova requisição agendada na base com sucesso!", "success")
    except Exception as e:
        print(f"❌ Falha crítica ao inserir novo registro no banco: {e}")
        flash("Erro interno ao persistir dados no banco operacional.", "danger")
        
    return redirect(url_for("index"))


@app.route('/download_modelo_excel')
def download_modelo_excel():
    """Gera uma planilha Excel estruturada baseada no Código do Produto Base com flag de concorrência."""
    if "usuario_logado" not in session: 
        return redirect(url_for('index'))
        
    try:
        # Colunas com a nova flag de controle inclusa
        exemplo = {
            'codigo_produto_base': ['12345', '67890'],
            'cidade': ['Porto Seguro', 'Salvador'],
            'dias_frequencia': [7, 14],
            'importar_concorrentes': ['Sim', 'Não']  # <-- ADICIONE ESTA LINHA SE NÃO ESTIVER LÁ
        }
        
        df_modelo = pd.DataFrame(exemplo)
        saida_excel = BytesIO()
        with pd.ExcelWriter(saida_excel, engine='openpyxl') as writer:
            df_modelo.to_excel(writer, index=False, sheet_name='Importar_Requisicoes')
            
        saida_excel.seek(0)
        return send_file(
            saida_excel,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name="Modelo_Importacao_Inteligente.xlsx"
        )
    except Exception as e:
        print(f"❌ Falha ao gerar modelo de planilha: {e}")
        flash("Não foi possível gerar o modelo de download.", "danger")
        return redirect(url_for("index"))

@app.route("/importar_excel_rapido", methods=["POST"])
def importar_excel_rapido():
    if "usuario_logado" not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autorizado"}), 401
        
    global status_importacao
    file = request.files.get("planilha_ean")
    
    if file and file.filename.endswith(('.xlsx', '.xls')):
        try:
            df = pd.read_excel(file)
            df.columns = [c.strip().lower() for c in df.columns]
            
            usr_dono = session.get("usuario_logado", "admin")
            perfil_ativo = session.get("perfil_logado", "USER")
            
            with GerenciadorConexao() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT limite_maximo FROM usuario WHERE LOWER(nome) = %s", (usr_dono.lower(),))
                    res_usuario = cur.fetchone()
                    limite_permitido = res_usuario[0] if res_usuario else 100
                    
                    if perfil_ativo != 'ADMIN':
                        cur.execute("SELECT COUNT(*) FROM dados WHERE LOWER(usuario_dono) = %s", (usr_dono.lower(),))
                        qtd_cadastrada = cur.fetchone()[0]
                        if (qtd_cadastrada + len(df)) > limite_permitido:
                            return jsonify({"sucesso": False, "mensagem": f"Limite máximo de {limite_permitido} seria ultrapassado."}), 400

            # Reseta as variáveis de monitoramento antes de disparar a Thread
            status_importacao["progresso"] = 0
            status_importacao["status"] = "Carregando planilha..."
            
            # Dispara o processamento em segundo plano sem travar a requisição HTTP
            thread = threading.Thread(target=executar_importacao_segundo_plano, args=(df, usr_dono, perfil_ativo, limite_permitido))
            thread.start()
            
            return jsonify({"sucesso": True, "mensagem": "Processamento iniciado."})
            
        except Exception as e:
            return jsonify({"sucesso": False, "mensagem": str(e)}), 500
            
    return jsonify({"sucesso": False, "mensagem": "Arquivo inválido."}), 400


@app.route("/progresso_importacao")
def progresso_importacao():
    if "usuario_logado" not in session:
        return jsonify({"sucesso": False, "mensagem": "Não autorizado"}), 401
        
    global status_importacao
    return jsonify(status_importacao)

@app.route("/importar_excel", methods=["POST"])
def importar_excel():
    if "usuario_logado" not in session:
        return redirect(url_for("index"))
        
    file = request.files.get("planilha_ean")
    if file and file.filename.endswith(('.xlsx', '.xls')):
        try:
            df = pd.read_excel(file)
            df.columns = [c.strip().lower() for c in df.columns]
            
            usr_dono = session.get("usuario_logado", "admin")
            perfil_ativo = session.get("perfil_logado", "USER")
            
            itens_cadastrados = 0
            itens_ignorados = 0
            
            with GerenciadorConexao() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT limite_maximo FROM usuario WHERE LOWER(nome) = %s", (usr_dono.lower(),))
                    res_usuario = cur.fetchone()
                    limite_permitido = res_usuario[0] if res_usuario else 100
                    
                    if perfil_ativo != 'ADMIN':
                        cur.execute("SELECT COUNT(*) FROM dados WHERE LOWER(usuario_dono) = %s", (usr_dono.lower(),))
                        qtd_cadastrada = cur.fetchone()[0]
                        if (qtd_cadastrada + len(df)) > limite_permitido:
                            flash(f"Importação negada! Limite máximo de {limite_permitido} seria ultrapassado.", "danger")
                            return redirect(url_for("index"))

                    for _, row in df.iterrows():
                        cod_prod = str(row.get('codigo_produto_base', '')).strip()
                        if not cod_prod or cod_prod == 'nan':
                            continue
                            
                        cidade = str(row.get('cidade', 'Não Informada')).strip()
                        valor_flag_bruto = str(row.get('importar_concorrentes', '')).strip().lower()
                        flag_concorrente = "" if valor_flag_bruto == "nan" else valor_flag_bruto
                        
                        try:
                            dias = int(row.get('dias_frequencia', 1))
                        except:
                            dias = 1
                            
                        cur.execute("""
                            SELECT ean_principal, ean_concorrente_1, ean_concorrente_2, ean_concorrente_3
                            FROM produtos WHERE LOWER(codigo_produto) = %s
                        """, (cod_prod.lower(),))
                        prod_dados = cur.fetchone()
                        
                        if not prod_dados or not prod_dados[0] or str(prod_dados[0]).strip() == "-":
                            itens_ignorados += 1
                            continue
                            
                        ean_principal = str(prod_dados[0]).strip()

                        # 🎯 ALTERADO: proxima_pesquisa vira CURRENT_TIMESTAMP e status vira 'Pendente' para rodar na hora
                        query_pai = """
                            INSERT INTO dados (codigo_barras, cidade, proxima_pesquisa, dias, data_cadastro, usuario_dono, status) 
                            VALUES (%s, %s, CURRENT_TIMESTAMP, %s, CURRENT_TIMESTAMP, %s, 'Pendente')
                            RETURNING id
                        """
                        cur.execute(query_pai, (ean_principal, cidade, dias, usr_dono))
                        novo_id = cur.fetchone()[0]
                        
                        concorrentes_lista = []
                        if flag_concorrente == "sim":
                            for idx in range(1, 4):  # Check ean_concorrente_1, ean_concorrente_2, ean_concorrente_3
                                if len(prod_dados) > idx and prod_dados[idx] and str(prod_dados[idx]).strip() != "-":
                                    concorrentes_lista.append(str(prod_dados[idx]).strip())
                                    
                        if concorrentes_lista:
                            cur.executemany(
                                "INSERT INTO dados_concorrentes (dados_id, ean_concorrente) VALUES (%s, %s)",
                                [(novo_id, ean) for ean in concorrentes_lista]
                            )
                        else:
                            cur.execute("INSERT INTO dados_concorrentes (dados_id, ean_concorrente) VALUES (%s, 'SEM_CONCORRENTES')", (novo_id,))
                            
                        itens_cadastrados += 1
                        
                conn.commit()
            
            msg = f"Sucesso! {itens_cadastrados} agendamentos inteligentes importados."
            if itens_ignorados > 0:
                msg += f" ({itens_ignorados} códigos não cadastrados na base de produtos foram ignorados)."
            flash(msg, "success" if itens_cadastrados > 0 else "warning")
            
        except Exception as e:
            print(f"❌ Erro ao processar arquivo de importação inteligente com flag: {e}")
            flash("Falha crítica ao ler a estrutura ou dados da planilha.", "danger")
            
    return redirect(url_for("index"))

            

@app.route('/login', methods=['POST'])
def realizar_login():
    u = request.form.get('usuario', '').strip().lower()
    s = request.form.get('senha', '').strip()
    
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT nome, senha, ativo, primeiro_acesso FROM usuario WHERE LOWER(nome) = %s", (u,))
                res = cur.fetchone()
                
        if res:
            if res["ativo"] == 0:
                flash("Sua conta está bloqueada provisoriamente! Contate o suporte.", "danger")
                return redirect(url_for('index'))
                
            if s == res["senha"] or (res["senha"].startswith('scrypt:') and check_password_hash(res["senha"], s)):
                session.clear()
                session['usuario_logado'] = str(res["nome"])
                session['perfil_logado'] = "ADMIN" if str(res["nome"]).lower() == "admin" else "USER"
                session['forcar_troca_senha'] = bool(res["primeiro_acesso"] == 1)
                return redirect(url_for('index'))
                
        flash("Credenciais de acesso incorretas!", "danger")
    except Exception as e:
        print(f"⚠️ Falha operacional no login: {e}")
        flash("Erro ao processar validação de perfil.", "danger")
        
    return redirect(url_for('index'))


@app.route("/alterar_senha_primeiro_acesso", methods=["POST"])
def alterar_senha_primeiro_acesso():
    if "usuario_logado" not in session:
        return redirect(url_for('index'))
        
    usuario = session["usuario_logado"]
    nova_senha = request.form.get("nova_senha", "").strip()
    confirmar_senha = request.form.get("confirmar_senha", "").strip()
    
    if not nova_senha or nova_senha != confirmar_senha:
        flash("As senhas digitadas não conferem ou estão em branco!", "danger")
        return redirect(url_for('index'))
        
    senha_criptografada = generate_password_hash(nova_senha)
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE usuario
                    SET senha = %s, primeiro_acesso = 0
                    WHERE LOWER(nome) = %s
                """, (senha_criptografada, usuario.lower()))
            conn.commit()
            
        session['forcar_troca_senha'] = False
        flash("Nova credencial gravada. Login liberado!", "success")
    except Exception as e:
        print(f"❌ Erro ao atualizar senha no banco: {e}")
        flash("Impossível atualizar credenciais no momento.", "danger")
        
    return redirect(url_for('index'))


@app.route("/editar", methods=["POST"])
def editar_requisicao():
    if "usuario_logado" not in session:
        return redirect(url_for('index'))
        
    usr_dono = session["usuario_logado"]
    id_a = request.form.get("id")
    cb = request.form.get("codigo_barras", "").strip()
    cid = request.form.get("cidade")
    dias = request.form.get("dias_pesquisa")
    
    eans_brutos = request.form.getlist("eans_concorrentes[]")
    eans_lista = []
    
    for ean_item in eans_brutos:
        if "," in ean_item:
            eans_lista.extend([e.strip() for e in ean_item.split(",") if e.strip()])
        else:
            if ean_item.strip():
                eans_lista.append(ean_item.strip())
                
    if not ean_valido(cb):
        flash("Falha ao salvar: O Código de barras editado é inválido.", "danger")
        return redirect(url_for("index"))
        
    eans_validos = [e for e in eans_lista if ean_valido(e)]
    dn = int(dias) if dias and dias.isdigit() else 1
    
    try:
        proxima_pesquisa = (datetime.now() + timedelta(days=dn)).date()
        
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                # 1. Atualiza a requisição pai garantindo a trava do dono se não for admin
                if session.get("perfil_logado") == "ADMIN":
                    cur.execute("""
                        UPDATE dados
                        SET codigo_barras = %s, cidade = %s, dias = %s, proxima_pesquisa = %s
                        WHERE id = %s
                    """, (cb, cid, dn, proxima_pesquisa, id_a))
                else:
                    cur.execute("""
                        UPDATE dados
                        SET codigo_barras = %s, cidade = %s, dias = %s, proxima_pesquisa = %s
                        WHERE id = %s AND LOWER(usuario_dono) = %s
                    """, (cb, cid, dn, proxima_pesquisa, id_a, usr_dono.lower()))
                
                # 2. Remove os concorrentes antigos vinculados a esta tarefa
                cur.execute("DELETE FROM dados_concorrentes WHERE dados_id = %s", (id_a,))
                
                # 3. Se não veio concorrente manual, reconstrói com base no catálogo do próprio usuário
                if not eans_validos:
                    cur.execute("""
                        SELECT ean_concorrente_1, ean_concorrente_2, ean_concorrente_3 
                        FROM produtos 
                        WHERE ean_principal = %s AND LOWER(usuario_dono) = %s AND ativo = 1
                    """, (cb, usr_dono.lower()))
                    prod_base = cur.fetchone()
                    if prod_base:
                        for val in prod_base:
                            if val and str(val).strip() != "-" and str(val).strip() != str(cb).strip():
                                eans_validos.append(str(val).strip())

                if eans_validos:
                    cur.executemany(
                        "INSERT INTO dados_concorrentes (dados_id, ean_concorrente) VALUES (%s, %s)",
                        [(id_a, e) for e in eans_validos]
                    )
                else:
                    cur.execute("INSERT INTO dados_concorrentes (dados_id, ean_concorrente) VALUES (%s, 'SEM_CONCORRENTES')", (id_a,))
                    
            conn.commit()
        flash("Registro de concorrência alterado com sucesso!", "success")
    except Exception as e:
        print(f"❌ Erro na rota /editar: {e}")
        flash("Falha ao modificar dados do item.", "danger")
        
    return redirect(url_for("index"))


@app.route('/logout')
def logout():
    """Limpa a sessão ativa do usuário e redireciona para a tela de login."""
    session.clear()
    return redirect(url_for('index'))

@app.route("/cadastrar_usuario", methods=["GET", "POST"])
@app.route("/admin/usuarios", methods=["GET", "POST"])
def admin_usuarios():
    """Painel administrativo para listagem e cadastro de operadores do sistema."""
    if session.get("perfil_logado") != "ADMIN":
        return "Acesso Restrito.", 403
        
    # =========================================================================
    # LÓGICA DE INSERÇÃO: PROCESSA O FORMULÁRIO (POST)
    # =========================================================================
    if request.method == "POST":
        novo_usuario = request.form.get("usuario", "").strip()
        nova_senha = request.form.get("senha", "").strip()
        novo_email = request.form.get("email", "").strip()  # 📥 CAPTURA O E-MAIL DO FORMULÁRIO
        limite_maximo = request.form.get("limite_maximo", "100")
        perfil = request.form.get("perfil", "USER").strip().upper() 
        
        if novo_usuario and nova_senha:
            try:
                senha_criptografada = generate_password_hash(nova_senha)
                
                with GerenciadorConexao() as conn:
                    with conn.cursor() as cur:
                        # 📝 ADICIONADO O CAMPO EMAIL E O SEU RESPECTIVO %s NA QUERY
                        cur.execute(
                            """INSERT INTO usuario (nome, senha, email, limite_maximo, primeiro_acesso, ativo, perfil) 
                               VALUES (%s, %s, %s, %s, 1, 1, %s)""",
                            (novo_usuario, senha_criptografada, novo_email, int(limite_maximo), perfil)
                        )
                    conn.commit()
                flash(f"Operador '{novo_usuario}' cadastrado com sucesso como {perfil}!", "success")
            except Exception as e:
                print(f"❌ Falha ao inserir novo usuário: {e}")
                flash("Erro ao gravar novo usuário no banco de dados.", "danger")
        else:
            flash("Por favor, preencha todos os campos obrigatórios.", "warning")
            
        return redirect(url_for("index", tela="usuarios", sucesso="true"))

    # =========================================================================
    # LÓGICA DE LEITURA (GET)
    # =========================================================================
    usuarios = []
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 🔍 ADICIONADO u.email NA CONSULTA E NO GROUP BY
                sql_calculo = """
                    SELECT 
                        u.id, u.nome, u.email, u.limite_maximo, u.ativo, u.perfil,
                        COUNT(d.id) as cadastradas,
                        SUM(CASE WHEN LOWER(d.status) LIKE '%%conclui%%' OR LOWER(d.status) LIKE '%%processado%%' THEN 1 ELSE 0 END) as realizadas
                    FROM usuario u
                    LEFT JOIN dados d ON LOWER(d.usuario_dono) = LOWER(u.nome)
                    GROUP BY u.id, u.nome, u.email, u.limite_maximo, u.ativo, u.perfil
                    ORDER BY u.id DESC
                """
                cur.execute(sql_calculo)
                rows = cur.fetchall()
                for row in rows:
                    limite_seguro = int(row["limite_maximo"]) if row["limite_maximo"] and int(row["limite_maximo"]) > 0 else 1
                    usuarios.append({
                        "ID": row["id"], 
                        "NOME": row["nome"], 
                        "EMAIL": row["email"] if row["email"] else "Não Informado", # 📥 REPASSA PARA O TEMPLATE
                        "LIMITE_MAXIMO": limite_seguro,
                        "ATIVO": row["ativo"], "PERFIL": row["perfil"],
                        "CADASTRADAS": int(row["cadastradas"]),
                        "REALIZADAS": int(row["realizadas"]) if row["realizadas"] else 0
                    })
    except Exception as e:
        print(f"❌ Falha de leitura analítica: {e}")
        
    return render_template("adm_usuarios.html", usuarios=usuarios, tela="usuarios")


@app.route("/reiniciar_requisicao", methods=["POST"])
@app.route("/reiniciar_requisicao/<int:id_alvo>", methods=["GET"])
def reiniciar_requisicao(id_alvo=None):
    if "usuario_logado" not in session:
        return redirect(url_for('index'))
        
    ids_alvo = [id_alvo] if id_alvo else request.form.getlist("ids_excluir[]") 
    if not ids_alvo:
        flash("Nenhuma requisição foi selecionada para reprocessamento!", "warning")
        return redirect(url_for("index"))

    try:
        usuario_ativo = session.get("usuario_logado")
        perfil_ativo = session.get("perfil_logado", "USER")
        ids_mapeados = list(map(int, ids_alvo))
        
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                # REGRA CRUCIAL FILTRADA: O status obrigatoriamente precisa conter 'Erro' (case-insensitive)
                if perfil_ativo == 'ADMIN':
                    query = """
                        UPDATE dados 
                        SET status = 'Pendente', proxima_pesquisa = CURRENT_DATE 
                        WHERE id = ANY(%s) AND LOWER(status) LIKE '%%erro%%'
                    """
                    cur.execute(query, (ids_mapeados,))
                else:
                    query = """
                        UPDATE dados 
                        SET status = 'Pendente', proxima_pesquisa = CURRENT_DATE 
                        WHERE id = ANY(%s) AND LOWER(usuario_dono) = %s AND LOWER(status) LIKE '%%erro%%'
                    """
                    cur.execute(query, (ids_mapeados, usuario_ativo.lower()))
            
            # Captura quantas linhas realmente foram afetadas pela alteração
            linhas_alteradas = cur.rowcount
            conn.commit()
            
        if linhas_alteradas > 0:
            flash(f"{linhas_alteradas} agendamento(s) com erro reiniciado(s) com sucesso!", "success")
        else:
            flash("Nenhum dos itens selecionados possuía o status de 'Erro'. Nada foi alterado.", "warning")
            
    except Exception as e:
        print(f"❌ Erro ao reiniciar requisições: {e}")
        flash("Não foi possível reiniciar o processamento dos itens.", "danger")
        
    return redirect('/?tela=painel')


@app.route("/deletar_em_bloco", methods=["POST"])
def deletar_bloco_requisicao():
    """Remove múltiplas requisições selecionadas na tabela operando em lote."""
    if "usuario_logado" not in session:
        return redirect(url_for('index'))
        
    # CORREÇÃO: Mudado de "usuarios_deletar[]" para "ids_excluir[]" para espelhar o HTML
    ids_selecionados = request.form.getlist("ids_excluir[]")
    if not ids_selecionados:
        flash("Nenhuma requisição foi selecionada para exclusão!", "warning")
        return redirect(url_for("index"))
        
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                cur.executemany("DELETE FROM dados_concorrentes WHERE dados_id = %s", [(idx,) for idx in ids_selecionados])
                cur.executemany("DELETE FROM dados WHERE id = %s", [(idx,) for idx in ids_selecionados])
            conn.commit()
        flash(f"{len(ids_selecionados)} requisições excluídas com sucesso em bloco!", "success")
    except Exception as e:
        print(f"❌ Erro na deleção em lote: {e}")
        flash("Falha parcial ao remover itens em bloco.", "danger")
        
    return redirect('/?tela=painel')


@app.route("/deletar/<int:id_alvo>")
@app.route("/deletar_individual/<int:id_alvo>")
def deletar_requisicao(id_alvo):
    """Remove uma única requisição selecionada a partir do ID operacional."""
    if "usuario_logado" not in session:
        return redirect(url_for('index'))
        
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM dados_concorrentes WHERE dados_id = %s", (id_alvo,))
                cur.execute("DELETE FROM dados WHERE id = %s", (id_alvo,))
            conn.commit()
        from flask import flash
        flash("Requisição removida com sucesso!", "success")
    except Exception as e:
        print(f"❌ Erro na deleção de ID {id_alvo}: {e}")
        from flask import flash
        flash("Não foi possível excluir o item individualmente.", "danger")
        
    return redirect('/?tela=painel')


@app.route('/admin/suspender/<int:id>')
def suspender_usuario(id):
    """Inverte o status ativo/inativo de um operador do sistema."""
    if session.get("perfil_logado") != "ADMIN":
        return "Acesso Restrito.", 403
        
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ativo FROM usuario WHERE id = %s", (id,))
                res = cur.fetchone()
                
                if res is not None:
                    # Aceita o retorno tanto em tupla padrão quanto em RealDictCursor
                    status_atual = res[0] if isinstance(res, tuple) else res["ativo"]
                    novo_status = 0 if int(status_atual) == 1 else 1
                    
                    cur.execute("UPDATE usuario SET ativo = %s WHERE id = %s", (novo_status, id))
                    
                    if novo_status == 0:
                        flash("Operador suspenso com sucesso!", "success")
                    else:
                        flash("Operador reativado com sucesso!", "success")
            conn.commit()
    except Exception as e:
        print(f"❌ Erro ao alternar status do usuário ID {id}: {e}")
        flash("Não foi possível alterar o status do operador.", "danger")
        
        # Procure o final da função def suspender_usuario(id):
    # APAGUE a linha antiga que tinha '/admin/usuarios...' e IMPLANTE esta:
    return redirect('/?tela=usuarios&sucesso=true')


@app.route("/admin/deletar_usuario/<int:id>")
def deletar_operador_final(id):  # MUDADO APENAS O NOME DA FUNÇÃO AQUI PARA BLINDAR CONFLITOS
    if session.get("perfil_logado") != "ADMIN":
        return "Acesso Restrito.", 403
        
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                # 1. Puxa o nome do usuário antes de deletar
                cur.execute("SELECT nome FROM usuario WHERE id = %s", (id,))
                res_user = cur.fetchone()
                
                if res_user:
                    nome_usuario_alvo = res_user[0] if isinstance(res_user, tuple) else res_user.get("nome")
                    
                    if nome_usuario_alvo:
                        # 2. Limpeza em cascata manual na base de dados
                        cur.execute("""
                            DELETE FROM dados_concorrentes 
                            WHERE dados_id IN (SELECT id FROM dados WHERE LOWER(usuario_dono) = %s)
                        """, (str(nome_usuario_alvo).lower(),))
                        
                        cur.execute("DELETE FROM dados WHERE LOWER(usuario_dono) = %s", (str(nome_usuario_alvo).lower(),))
                
                # 3. Deleta o operador sem travar chaves no PostgreSQL
                cur.execute("DELETE FROM usuario WHERE id = %s", (id,))
                
            conn.commit()
        flash("Operador e histórico de requisições removidos com sucesso!", "success")
    except Exception as e:
        print(f"❌ Erro crítico na deleção do usuário ID {id}: {e}")
        flash("Não foi possível excluir este operador do sistema.", "danger")
        
        # Mude a última linha da função de: return redirect('/?tela=usuarios&sucesso=true')
    # Para esta linha abaixo:
    return redirect('/?tela=usuarios&sucesso=true')
    

@app.route("/admin/editar_usuario/<int:id_alvo>", methods=["POST"])
def editar_operador_sistema(id_alvo):
    if session.get("perfil_logado") != "ADMIN":
        return "Acesso Restrito.", 403
        
    # 1. Captura os valores limpando espaços em branco
    nome = request.form.get("usuario") or request.form.get("nome") or request.form.get("NOME")
    nome = str(nome).strip() if nome else ""
    
    email = request.form.get("email") or request.form.get("EMAIL")
    email = str(email).strip() if email else ""
    
    limite = request.form.get("limite_maximo") or request.form.get("LIMITE_MAXIMO") or "100"
    limite = str(limite).strip()
    
    # FORÇADO: Garante que o limite seja estritamente um número inteiro puro para o PostgreSQL
    limite_num = int(float(limite)) if limite.replace('.', '', 1).isdigit() else 100
        
    perfil = request.form.get("perfil") or request.form.get("PERFIL") or "USER"
    perfil = str(perfil).strip().upper()
    
    senha_nova = request.form.get("senha") or request.form.get("SENHA") or ""
    senha_nova = str(senha_nova).strip()

    try:
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                # Caso o administrador tenha digitado uma senha nova, criptografa e salva
                if senha_nova and senha_nova != "••••••••" and senha_nova != "":
                    senha_cripto = generate_password_hash(senha_nova)
                    cur.execute("""
                        UPDATE usuario 
                        SET nome = %s, email = %s, limite_maximo = %s::INTEGER, perfil = %s, senha = %s
                        WHERE id = %s
                    """, (nome, email, limite_num, perfil, senha_cripto, int(id_alvo)))
                else:
                    # Se não mudou a senha, atualiza os dados incluindo o cast manual (::INTEGER)
                    cur.execute("""
                        UPDATE usuario 
                        SET nome = %s, email = %s, limite_maximo = %s::INTEGER, perfil = %s
                        WHERE id = %s
                    """, (nome, email, limite_num, perfil, int(id_alvo)))
                        
            conn.commit() # Garante a persistência imediata na base física
        flash("Operador atualizado com sucesso!", "success")
    except Exception as e:
        print(f"❌ Erro crítico no UPDATE do banco de dados: {e}")
        flash("Não foi possível atualizar este operador no sistema.", "danger")
            
    return redirect('/?tela=usuarios&sucesso=true')


@app.route("/produtos/salvar", methods=["POST"])
@app.route("/cadastrar_produto_base", methods=["POST"])
def cadastrar_produto_base():
    if "usuario_logado" not in session and "usuario_dono" not in session: 
        return redirect(url_for('index'))
        
    cod = request.form.get("codigo_produto", "").strip()
    ean_p = request.form.get("ean_principal", "").strip()
    desc = request.form.get("descricao_item", "").strip()
    categoria = request.form.get("categoria", "").strip()  # 💡 NOVO: Captura o campo categoria do formulário
    
    # 🎯 ALINHADO COM OS NOVOS NAMES DO FORMULÁRIO DO TOPO:
    p_tabela_raw = request.form.get("preco_tabela", "0").replace(",", ".").strip()
    p_minimo_raw = request.form.get("preco_minimo", "0").replace(",", ".").strip()
    p_index_raw = request.form.get("preco_index", "0").replace(",", ".").strip()
    
    e1 = request.form.get("ean_1", "").strip()
    e2 = request.form.get("ean_2", "").strip()
    e3 = request.form.get("ean_3", "").strip()
    
    if cod and desc:
        try:
            with GerenciadorConexao() as conn:
                with conn.cursor() as cur:
                    # Captura o usuário de forma resiliente
                    usr_dono = session.get("usuario_logado") or session.get("usuario_dono") or "admin"
                    
                    # 💡 ATUALIZADO: Incluída a coluna 'categoria' e seu respectivo %s no INSERT
                    cur.execute("""
                        INSERT INTO produtos (codigo_produto, ean_principal, descricao_item, categoria, preco_tabela, preco_minimo, preco_index, ean_concorrente_1, ean_concorrente_2, ean_concorrente_3, usuario_dono) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (cod, ean_p, desc, categoria, float(p_tabela_raw or 0), float(p_minimo_raw or 0), float(p_index_raw or 0), e1, e2, e3, usr_dono))
                conn.commit()
            flash("Produto adicionado à base física com sucesso!", "success")
        except Exception as e: 
            print(f"❌ Erro cadastrar prod: {e}")
            flash("Erro operacional ao salvar produto no banco.", "danger")
            
    return redirect('/?tela=produtos')





@app.route("/deletar_produto_base/<int:id_alvo>")
def deletar_produto_base(id_alvo):
    if "usuario_logado" not in session: 
        return redirect(url_for('index'))
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur: 
                # Em vez de DELETE, fazemos um Soft Delete (Desativação)
                cur.execute("UPDATE produtos SET ativo = 0 WHERE id = %s", (id_alvo,))
            conn.commit()
        flash("Produto removido do catálogo com sucesso!", "success")
    except Exception as e: 
        print(f"❌ Erro deletar prod: {e}")
        flash("Erro ao remover produto do banco.", "danger")
        
    return redirect('/?tela=produtos')

        
    return redirect('/?tela=produtos')

@app.route("/produtos/baixar_modelo")
def baixar_modelo_produtos():
    """Gera e entrega o arquivo Excel modelo estruturado para cadastro de produtos."""
    if "usuario_logado" not in session:
        return redirect(url_for('index'))
    try:
        saida = BytesIO()
        # 💡 ATUALIZADO: Adicionada a coluna 'categoria' na estrutura do DataFrame de exemplo
        df_modelo = pd.DataFrame({
            'codigo_produto': ['COD-ARROZ-01', 'COD-FEIJAO-02'],
            'ean_principal': ['7891000123456', '7892000654321'],
            'descricao_item': ['Arroz Integral 5kg', 'Feijão Carioca 1kg'],
            'categoria': ['Alimentos', 'Alimentos'],  # <-- Nova linha
            'preco_tabela': [22.50, 8.90],
            'preco_minimo': [35.00, 14.50],
            'preco_index': [0.0, 0.0],
            'ean_concorrente_1': ['7891000999991', '7892000888882'],
            'ean_concorrente_2': ['', ''],
            'ean_concorrente_3': ['', '']
        })
        
        with pd.ExcelWriter(saida, engine='openpyxl') as writer:
            df_modelo.to_excel(writer, index=False)
            
        saida.seek(0)
        return send_file(
            saida, 
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
            as_attachment=True, 
            download_name="Modelo_Importacao_Produtos.xlsx"
        )
    except Exception as e:
        print(f"❌ Erro ao gerar modelo de produtos: {e}")
        flash("Erro interno ao gerar o arquivo de modelo.", "danger")
        return redirect('/?tela=produtos')



@app.route("/produtos/importar_lote", methods=["POST"])
def importar_produtos_lote():
    """Processa o arquivo Excel enviado e insere múltiplos produtos em lote no Postgres."""
    if "usuario_logado" not in session:
        return redirect(url_for('index'))
        
    arquivo = request.files.get("planilha_produtos")
    if arquivo and arquivo.filename.endswith(('.xlsx', '.xls')):
        try:
            df = pd.read_excel(arquivo)
            df.columns = [str(c).strip().lower() for c in df.columns]
            
            sucessos = 0
            with GerenciadorConexao() as conn:
                with conn.cursor() as cur:
                    for _, row in df.iterrows():
                        cod = str(row.get('codigo_produto', '')).strip()
                        ean_p = str(row.get('ean_principal', '')).strip().split('.')[0]
                        desc = str(row.get('descricao_item', '')).strip()
                        # 💡 NOVO: Mapeia e higieniza a coluna categoria do Excel
                        categoria = str(row.get('categoria', '')).strip()
                        
                        if not cod or not desc or cod == 'nan' or desc == 'nan':
                            continue
                            
                        p_tab = float(row.get('preco_tabela', row.get('preco_minimo', 0))) if pd.notnull(row.get('preco_tabela', row.get('preco_minimo'))) else 0.0
                        p_min = float(row.get('preco_minimo', row.get('preco_maximo', 0))) if pd.notnull(row.get('preco_minimo', row.get('preco_maximo'))) else 0.0
                        p_idx = float(row.get('preco_index', 0)) if pd.notnull(row.get('preco_index')) else 0.0
                        
                        e1 = str(row.get('ean_concorrente_1', '')).strip().split('.')[0] if pd.notnull(row.get('ean_concorrente_1')) else ""
                        e2 = str(row.get('ean_concorrente_2', '')).strip().split('.')[0] if pd.notnull(row.get('ean_concorrente_2')) else ""
                        e3 = str(row.get('ean_concorrente_3', '')).strip().split('.')[0] if pd.notnull(row.get('ean_concorrente_3')) else ""
                        
                        ean_p = "" if ean_p == 'nan' else ean_p
                        categoria = "" if categoria == 'nan' else categoria # <-- Trata nan do Pandas
                        e1 = "" if e1 == 'nan' else e1
                        e2 = "" if e2 == 'nan' else e2
                        e3 = "" if e3 == 'nan' else e3

                        usr_dono = session.get("usuario_logado", "admin")

                        # 💡 ATUALIZADO: Incluído a coluna 'categoria' e seu respectivo %s no INSERT em lote
                        cur.execute("""
                            INSERT INTO produtos (codigo_produto, ean_principal, descricao_item, categoria, preco_tabela, preco_minimo, preco_index, ean_concorrente_1, ean_concorrente_2, ean_concorrente_3, usuario_dono) 
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (cod, ean_p, desc, categoria, p_tab, p_min, p_idx, e1, e2, e3, usr_dono))
                        sucessos += 1
                        
                conn.commit()
            flash(f"Sucesso! {sucessos} produtos foram importados em lote para a base física.", "success")
        except Exception as e:
            print(f"❌ Erro na importação em lote de produtos: {e}")
            flash("Falha ao processar o arquivo Excel. Verifique a estrutura das colunas.", "danger")
    else:
        flash("Por favor, selecione um arquivo válido no formato .xlsx ou .xls", "warning")
        
    return redirect('/?tela=produtos')



@app.route("/api/buscar_produto_base/<codigo>")
def api_buscar_produto_base(codigo):
    """Busca um produto na base pelo código e filtra pelo usuário logado de forma resiliente."""
    usuario_ativo = session.get("usuario_logado") or session.get("usuario_dono")
    perfil_ativo = session.get("perfil_logado", "USER").upper() # 🔐 Captura o nível de acesso real
    
    if not usuario_ativo:
        return {"erro": "Não autorizado"}, 401
        
    try:
        codigo_limpo = str(codigo).strip().lower()
        
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                # 🛡️ TRAVA OPERACIONAL ISOLADA:
                if perfil_ativo == 'ADMIN':
                    # O ADMIN ignora o dono e consegue verificar produtos de qualquer usuário (ex: cambuí)
                    cur.execute("""
                        SELECT codigo_produto, ean_principal, descricao_item, 
                               ean_concorrente_1, ean_concorrente_2, ean_concorrente_3
                        FROM produtos 
                        WHERE (LOWER(codigo_produto) = %s OR ean_principal = %s)
                          AND ativo = 1
                    """, (codigo_limpo, codigo_limpo))
                else:
                    # O usuário USER continua estritamente preso apenas aos SEUS próprios produtos cadastrados
                    cur.execute("""
                        SELECT codigo_produto, ean_principal, descricao_item, 
                               ean_concorrente_1, ean_concorrente_2, ean_concorrente_3
                        FROM produtos 
                        WHERE (LOWER(codigo_produto) = %s OR ean_principal = %s)
                          AND LOWER(usuario_dono) = %s
                          AND ativo = 1
                    """, (codigo_limpo, codigo_limpo, str(usuario_ativo).strip().lower()))
                
                prod = cur.fetchone()
                
                if prod:
                    concorrentes_detectados = []
                    ean_pai = str(prod[1]).strip() if prod[1] else ""
                    
                    for idx in range(3, 6):
                        if len(prod) > idx and prod[idx]:
                            valor_concorrente = str(prod[idx]).strip()
                            if valor_concorrente and valor_concorrente != "-" and valor_concorrente != ean_pai:
                                concorrentes_detectados.append(valor_concorrente)
                    
                    return {
                        "encontrado": True,
                        "codigo": prod[0],
                        "ean_principal": prod[1] if prod[1] else "-",
                        "descricao": prod[2],
                        "concorrentes": concorrentes_detectados
                    }
                else:
                    return {"encontrado": False, "mensagem": "Produto não cadastrado"}
    except Exception as e:
        print(f"❌ Erro crítico na API de busca de produto: {e}")
        return {"erro": "Erro interno no servidor"}, 500




@app.route("/produtos/editar", methods=["POST"])
def editar_produto_base():
    """Atualiza as informações de um produto existente no catálogo base preservando as travas."""
    if "usuario_logado" not in session: 
        return redirect(url_for('index'))
        
    id_alvo = request.form.get("id")
    cod = request.form.get("codigo_produto", "").strip()
    ean_p = request.form.get("ean_principal", "").strip()
    desc = request.form.get("descricao_item", "").strip()
    categoria = request.form.get("categoria", "").strip()  # 💡 NOVO: Captura do input de categoria do modal
    
    p_tabela_raw = request.form.get("preco_tabela", request.form.get("preco_minimo", "0")).replace(",", ".").strip()
    p_minimo_raw = request.form.get("preco_minimo_atualizado", request.form.get("preco_minimo", "0")).replace(",", ".").strip()
    
    if p_tabela_raw == p_minimo_raw and request.form.get("preco_maximo"):
        p_minimo_raw = request.form.get("preco_maximo").replace(",", ".").strip()

    p_index_raw = request.form.get("preco_index", "0")
    p_index_limpo = str(p_index_raw).replace(",", ".").strip()
    
    try:
        p_index_num = float(p_index_limpo) if p_index_limpo else 0.0
    except ValueError:
        p_index_num = 0.0
    
    e1 = request.form.get("ean_1", "").strip()
    e2 = request.form.get("ean_2", "").strip()
    e3 = request.form.get("ean_3", "").strip()
    
    if id_alvo and cod and desc:
        try:
            with GerenciadorConexao() as conn:
                with conn.cursor() as cur:
                    # 💡 ATUALIZADO: Adicionado 'categoria = %s' na query de UPDATE
                    cur.execute("""
                        UPDATE produtos 
                        SET codigo_produto = %s, 
                            ean_principal = %s, 
                            descricao_item = %s, 
                            categoria = %s, 
                            preco_tabela = %s::NUMERIC, 
                            preco_minimo = %s::NUMERIC, 
                            preco_index = %s::NUMERIC, 
                            ean_concorrente_1 = %s, 
                            ean_concorrente_2 = %s, 
                            ean_concorrente_3 = %s
                        WHERE id = %s
                    """, (cod, ean_p, desc, categoria, float(p_tabela_raw or 0), float(p_minimo_raw or 0), p_index_num, e1, e2, e3, int(id_alvo)))
                conn.commit()
            flash("Produto updated com sucesso no catálogo!", "success")
        except Exception as e:
            print(f"❌ Erro operacional crítico ao editar produto base ID {id_alvo}: {e}")
            flash("Erro operacional ao atualizar o produto no banco.", "danger")
            
    return redirect('/?tela=produtos')


@app.route('/dashboard')
def dashboard():
    if "usuario_logado" not in session:
        return redirect(url_for('index'))
    return render_template('dashboard.html')


if __name__ == "__main__":
    app.jinja_env.auto_reload = True
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    
    # Voltamos ao padrão normal e estável que funciona na sua máquina
    app.run(host="0.0.0.0", port=5000, debug=True)
