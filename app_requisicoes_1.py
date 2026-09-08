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

# ==============================================================================
# FUNÇÕES AUXILIARES DE SUPORTE
# ==============================================================================
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
                    # CONSULTA ESPELHADA: Adicionado a coluna 'perfil' na busca do SQL (Índice 3)
                    cur.execute("SELECT id, nome, senha, perfil FROM usuario WHERE LOWER(nome) = %s", (usuario_input.lower(),))
                    usuario_banco = cur.fetchone()
                    
                    if usuario_banco:
                        # MAPEAMENTO CORRETO DA TUPLA DO POSTGRESQL:
                        id_usuario_db = usuario_banco[0]
                        nome_usuario_db = usuario_banco[1]
                        senha_db = str(usuario_banco[2]).strip()
                        perfil_db = str(usuario_banco[3]).strip().upper() if usuario_banco[3] else "USER"
                        
                        # SISTEMA DE VALIDAÇÃO DUPLO:
                        eh_valido = False
                        if senha_db.startswith("scrypt:"):
                            eh_valido = check_password_hash(senha_db, senha_input)
                        else:
                            eh_valido = (senha_input == senha_db)
                        
                        if eh_valido:
                            session.clear()
                            session["usuario_logado"] = nome_usuario_db
                            session["usuario_id"] = id_usuario_db
                            session["perfil_logado"] = perfil_db  # Define o perfil real vindo do banco

                            
                            return redirect(url_for("index"))
            
        except Exception as err_login:
            print(f"❌ Erro crítico ao validar credenciais no banco: {err_login}")
            from flask import flash
            flash("Falha interna ao conectar na base de usuários.", "danger")
            return render_template("index.html", tela="login")

    # 2. BLOQUEIO DE SEGURANÇA: Se não enviou formulário e não está logado, barra o acesso
    if "usuario_logado" not in session: 
        return render_template("index.html", tela="login")


    # 3. CONTINUAÇÃO DO PAINEL (Lógica original de leitura de tarefas, métricas e cidades)
    busca = request.args.get("q", "").strip().lower()
    metrics = {"total": 0, "pendentes": 0, "andamento": 0, "concluidas": 0}
    tarefas = []
    
    # ... O restante do seu código do painel (Bloco 1 de cidades e Bloco 2 de tarefas) continua igual aqui embaixo ...

    
    cidades_opcoes = ["Itamaraju", "Itabela", "Eunápolis", "Teixeira de Freitas"]

    # Bloco 1: Tenta buscar as cidades dinamicamente da tabela cidades_sistema
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT cidade FROM cidades_sistema ORDER BY cidade ASC")
                rows_cidades = cur.fetchall()
                
                if rows_cidades:
                    cidades_opcoes = [str(r[0]).strip() for r in rows_cidades if r and r[0] is not None]
                    # print(f"✅ Cidades carregadas com sucesso do banco: {cidades_opcoes}")
                else:
                    print("⚠️ A tabela cidades_sistema retornou vazia. Usando lista padrão.")
    except Exception as err_cidades:
        print(f"❌ Erro ao buscar cidades_sistema no banco: {err_cidades}")

        # Bloco 2: Busca as requisições principais para listar na tabela
    try:
        usuario_ativo = session.get("usuario_logado")
        # Captura o perfil salvo na sessão (corrigido na rota de login)
        perfil_ativo = session.get("perfil_logado", "USER") 
        
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                # REGRA ALTERADA: Qualquer um com perfil ADMIN vê tudo da base de dados
                if perfil_ativo == 'ADMIN':
                    cur.execute(
                        "SELECT id, codigo_barras, cidade, status, data_cadastro, dias FROM dados ORDER BY id DESC"
                    )
                else:
                    cur.execute(
                        """SELECT id, codigo_barras, cidade, status, data_cadastro, dias 
                           FROM dados 
                           WHERE LOWER(usuario_dono) = %s 
                           ORDER BY id DESC""", 
                        (usuario_ativo.lower() if usuario_ativo else '',)
                    )

                    
                rows = cur.fetchall()
                
                for row in rows:
                    id_real = int(row[0])
                    ean_real = row[1]
                    cidade_real = row[2]
                    status_real = str(row[3]).strip()
                    data_real = row[4].strftime("%d/%m/%Y %H:%M") if hasattr(row[4], "strftime") else str(row[4])
                    dias_real = row[5] if len(row) > 5 else 1

                    item_tarefa = [
                        id_real,            
                        ean_real,           
                        "Sem concorrentes", 
                        cidade_real,        
                        status_real,        
                        data_real,          
                        dias_real,          
                        "", ""              
                    ]
                    
                    if busca:
                        if (busca in str(ean_real).lower()) or (busca in str(cidade_real).lower()):
                            tarefas.append(item_tarefa)
                    else:
                        tarefas.append(item_tarefa)

                    # Contagem das métricas baseada estritamente no universo visível do usuário
                    metrics["total"] += 1
                    if "concluid" in status_real.lower() or "processado" in status_real.lower():
                        metrics["concluidas"] += 1
                    elif "andamento" in status_real.lower():
                        metrics["andamento"] += 1
                    else:
                        metrics["pendentes"] += 1
                        
    except Exception as e:
        print(f"❌ Erro crítico na leitura filtrada da tabela dados: {e}")
        metrics = {"total": 0, "pendentes": 0, "andamento": 0, "concluidas": 0}

    return render_template("index.html", tela="painel", tarefas=tarefas, metrics=metrics, cidades_opcoes=cidades_opcoes, q=busca)


@app.route("/cadastrar", methods=["POST"])
def cadastrar_requisicao_unica():
    if "usuario_logado" not in session:
        return redirect(url_for('index'))
        
    usr_dono = session["usuario_logado"]
    cb = request.form.get("codigo_barras", "").strip()
    cid = request.form.get("cidade", "Não Informada").strip()
    dias_input = request.form.get("dias_pesquisa", "1")
    
    if not cb:
        flash("O Código de Barras (EAN) principal é obrigatório!", "danger")
        return redirect(url_for("index"))
        
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                                # -------------------------------------------------------------
                # VALIDAÇÃO DE LIMITE COOPERAÇÃO COM O BANCO
                # -------------------------------------------------------------
                # 1. Puxa o limite do usuário ativo
                cur.execute("SELECT limite_maximo FROM usuario WHERE LOWER(nome) = %s", (usr_dono.lower(),))
                res_usuario = cur.fetchone()
                limite_permitido = res_usuario[0] if res_usuario else 100
                
                # CORREÇÃO CRUCIAL: Agora qualquer usuário com perfil ADMIN ganha passe livre absoluto
                if session.get("perfil_logado") != "ADMIN":
                    # 2. Conta quantas requisições este usuário já tem cadastradas na tabela dados
                    cur.execute("SELECT COUNT(*) FROM dados WHERE LOWER(usuario_dono) = %s", (usr_dono.lower(),))
                    qtd_cadastrada = cur.fetchone()[0]
                    
                    # 3. Bloqueia a inserção se o limite já foi alcançado
                    if qtd_cadastrada >= limite_permitido:
                        flash(f"Falha ao cadastrar: Você atingiu o seu limite máximo permitido de {limite_permitido} requisições!", "danger")
                        return redirect(url_for("index"))
                # -------------------------------------------------------------

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

                px = datetime.now() + timedelta(days=dias)
                
                query_pai = """
                    INSERT INTO dados (codigo_barras, cidade, proxima_pesquisa, dias, data_cadastro, usuario_dono)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, %s) RETURNING id
                """
                cur.execute(query_pai, (cb, cid, px.date(), dias, usr_dono))
                
                retorno_id = cur.fetchone()
                id_puro = retorno_id[0] if retorno_id else None
                
                if eans_lista and id_puro:
                    cur.executemany(
                        "INSERT INTO dados_concorrentes (dados_id, ean_concorrente) VALUES (%s, %s)",
                        [(id_puro, str(e).strip()) for e in eans_lista if e.strip()]
                    )
                    
            conn.commit()
        flash("Nova requisição agendada na base com sucesso!", "success")
    except Exception as e:
        print(f"❌ Falha crítica ao inserir novo registro no banco: {e}")
        flash("Erro interno ao persistir dados no banco operacional.", "danger")
        
    return redirect(url_for("index"))


    # O return lê com sucesso absoluto pois as variáveis foram criadas no topo da função
    return render_template("index.html", tela="painel", tarefas=tarefas, metrics=metrics, cidades_opcoes=cidades_opcoes, q=busca)


@app.route('/download_modelo_excel')
def download_modelo_excel():
    """Gera uma planilha Excel estruturada modelo em branco e entrega para o usuário."""
    if "usuario_logado" not in session: 
        return redirect(url_for('index'))
        
    try:
        # Define as colunas padrão exigidas pelo script e robô de raspagem
        exemplo = {
            'codigo_barras': ['7891000123456'],
            'cidade': ['Salvador'],
            'dias':[7],
            'concorrentes': ['7891000999991, 7891000999992']
        }
        
        df_modelo = pd.DataFrame(exemplo)
        
        saida_excel = BytesIO()
        with pd.ExcelWriter(saida_excel, engine='openpyxl') as writer:
            df_modelo.to_excel(writer, index=False, sheet_name='Modelo_Importacao')
            
        saida_excel.seek(0)
        
        return send_file(
            saida_excel,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name="Modelo_Importacao_EAN.xlsx"
        )
    except Exception as e:
        print(f"❌ Falha ao gerar modelo de planilha: {e}")
        flash("Não foi possível gerar o modelo de download.", "danger")
        return redirect(url_for("index"))


@app.route("/importar_excel", methods=["POST"])
def importar_excel():
    if "usuario_logado" not in session:
        return redirect(url_for("index"))
        
    file = request.files.get("planilha_ean")
    if file and file.filename.endswith(('.xlsx', '.xls')):
        try:
            import pandas as pd
            df = pd.read_excel(file)
            df.columns = [c.strip().lower() for c in df.columns]
            
            usr_dono = session.get("usuario_logado", "admin")
            # Captura o perfil real armazenado na sessão do usuário logado
            perfil_ativo = session.get("perfil_logado", "USER")
            
            with GerenciadorConexao() as conn:
                with conn.cursor() as cur:
                    # ---------------------------------------------------------
                    # CHECAGEM DE LIMITE PARA PLANILHAS (RETIFICADA)
                    # ---------------------------------------------------------
                    cur.execute("SELECT limite_maximo FROM usuario WHERE LOWER(nome) = %s", (usr_dono.lower(),))
                    res_usuario = cur.fetchone()
                    limite_permitido = res_usuario[0] if res_usuario else 100
                    
                    # CORREÇÃO CRUCIAL: Agora verifica o perfil ADMIN na sessão para dar o passe livre absoluto
                    if perfil_ativo != 'ADMIN':
                        cur.execute("SELECT COUNT(*) FROM dados WHERE LOWER(usuario_dono) = %s", (usr_dono.lower(),))
                        qtd_cadastrada = cur.fetchone()[0]
                        
                        # Verifica se a quantidade atual somada com as linhas da planilha ultrapassa o limite
                        if (qtd_cadastrada + len(df)) > limite_permitido:
                            vagas_restantes = limite_permitido - qtd_cadastrada
                            vagas_restantes = 0 if vagas_restantes < 0 else vagas_restantes
                            flash(f"Importação negada! Você possui {qtd_cadastrada} itens e tentou enviar mais {len(df)}. Seu limite máximo é {limite_permitido} (Resta: {vagas_restantes}).", "danger")
                            return redirect(url_for("index"))

                    # ---------------------------------------------------------

                    for _, row in df.iterrows():
                        cb_bruto = str(row.get('codigo_barras', '')).strip()
                        codigo_barras = cb_bruto.split('.')[0] if '.' in cb_bruto and cb_bruto.split('.')[1] == '0' else cb_bruto
                        
                        if not codigo_barras or codigo_barras == 'nan':
                            continue
                            
                        cidade = str(row.get('cidade', '')).strip()
                        
                        try:
                            dias = int(row.get('dias', 1))
                        except:
                            dias = 1
                            
                        proxima_pesquisa = (datetime.now() + timedelta(days=dias)).date()

                        query_pai = """
                            INSERT INTO dados (codigo_barras, cidade, proxima_pesquisa, dias, data_cadastro, usuario_dono) 
                            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
                            RETURNING id
                        """
                        cur.execute(query_pai, (codigo_barras, cidade, proxima_pesquisa, dias, usr_dono))
                        novo_id = cur.fetchone()
                        
                        concorrentes_brutos = str(row.get('concorrentes', '')).strip()
                        if concorrentes_brutos and concorrentes_brutos != 'nan' and novo_id:
                            lista_concorrentes = [c.strip() for c in concorrentes_brutos.split(",") if c.strip()]
                            
                            if lista_concorrentes:
                                cur.executemany(
                                    "INSERT INTO dados_concorrentes (dados_id, ean_concorrente) VALUES (%s, %s)",
                                    [(novo_id[0], ean) for ean in lista_concorrentes]
                                )
                        
                conn.commit()
            flash(f"Lote de {len(df)} agendamentos importado com sucesso!", "success")
        except Exception as e:
            print(f"❌ Erro ao processar arquivo de importação Excel: {e}")
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
        
    id_a = request.form.get("id")
    cb = request.form.get("codigo_barras", "").strip()
    cid = request.form.get("cidade")
    dias = request.form.get("dias_pesquisa")
    
    # Captura os eans concorrentes vindos do input do formulário
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
    dn = int(dias) if dias and dias.isdigit() else 1 # Alterado padrão de 9999 para 1 para evitar congelar o robô
    
    try:
        # COMPORTAMENTO DINÂMICO: Calcula a nova data de pesquisa baseada nos dias editados
        proxima_pesquisa = (datetime.now() + timedelta(days=dn)).date()
        
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                # 1. Atualiza os dados do produto pai incluindo a nova data de execução
                cur.execute("""
                    UPDATE dados
                    SET codigo_barras = %s, cidade = %s, dias = %s, proxima_pesquisa = %s
                    WHERE id = %s
                """, (cb, cid, dn, proxima_pesquisa, id_a))
                
                # 2. Remove os concorrentes antigos associados
                cur.execute("DELETE FROM dados_concorrentes WHERE dados_id = %s", (id_a,))
                
                # 3. Insere os novos concorrentes validados se existirem
                if eans_validos:
                    cur.executemany(
                        "INSERT INTO dados_concorrentes (dados_id, ean_concorrente) VALUES (%s, %s)",
                        [(id_a, e) for e in eans_validos]
                    )
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
    # LÓGICA DE INSERÇÃO: PROCESSA O FORMULÁRIO (POST) RETIFICADA
    # =========================================================================
    if request.method == "POST":
        novo_usuario = request.form.get("usuario", "").strip()
        nova_senha = request.form.get("senha", "").strip()
        limite_maximo = request.form.get("limite_maximo", "100")
        
        # Captura e garante que o valor venha limpo e em letras maiúsculas
        perfil = request.form.get("perfil", "USER").strip().upper() 
        
        if novo_usuario and nova_senha:
            try:
                # CORREÇÃO CRUCIAL: Criptografa a senha para o validador de login aceitar e identificar o 1º acesso
                senha_criptografada = generate_password_hash(nova_senha)
                
                with GerenciadorConexao() as conn:
                    with conn.cursor() as cur:
                        # Forçamos explicitamente o primeiro_acesso como 1
                        cur.execute(
                            """INSERT INTO usuario (nome, senha, limite_maximo, primeiro_acesso, ativo, perfil) 
                               VALUES (%s, %s, %s, 1, 1, %s)""",
                            (novo_usuario, senha_criptografada, int(limite_maximo), perfil)
                        )
                    conn.commit()
                flash(f"Operador '{novo_usuario}' cadastrado com sucesso como {perfil}!", "success")
            except Exception as e:
                print(f"❌ Falha ao inserir novo usuário: {e}")
                flash("Erro ao gravar novo usuário no banco de dados.", "danger")
        else:
            flash("Por favor, preencha todos os campos obrigatórios.", "warning")
            
        return redirect(url_for("admin_usuarios"))

        # =========================================================================
    # LÓGICA DE LEITURA: CARREGA A LISTAGEM (GET) RETIFICADA
    # =========================================================================
    usuarios = []
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # ADICIONADO 'perfil' NA SELEÇÃO SQL
                cur.execute("SELECT id, nome, limite_maximo, ativo, perfil FROM usuario ORDER BY id DESC")
                for row in cur.fetchall():
                    usuarios.append({
                        "ID": row["id"],
                        "NOME": row["nome"],
                        "LIMITE_MAXIMO": row["limite_maximo"],
                        "ATIVO": row["ativo"],
                        "PERFIL": row["perfil"], # ADICIONADO AQUI PARA ENVIAR AO HTML
                        "CADASTRADAS": 0,
                        "REALIZADAS": 0
                    })
    except Exception as e:
        print(f"❌ Falha de leitura em operadores/admin: {e}")
        flash("Erro ao consultar registros do banco.", "danger")
        
    return render_template("adm_usuarios.html", usuarios=usuarios)



@app.route("/reiniciar_requisicao/<int:id_alvo>")
def reiniciar_requisicao(id_alvo):
    """Muda o status de um registro com erro ou aviso de volta para 'Pendente'."""
    if "usuario_logado" not in session:
        return redirect(url_for('index'))
        
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE dados 
                    SET status = 'Pendente', proxima_pesquisa = CURRENT_DATE 
                    WHERE id = %s
                """, (id_alvo,))
            conn.commit()
        flash("Agendamento reiniciado com sucesso para reprocessamento!", "success")
    except Exception as e:
        print(f"❌ Erro ao reiniciar requisição ID {id_alvo}: {e}")
        flash("Não foi possível reiniciar o processamento deste item.", "danger")
        
    return redirect(url_for("index"))

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
        
    return redirect(url_for("index"))


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
        
    return redirect(url_for("index"))


@app.route("/atualizar/<int:id_tarefa>")
def atualizar_requisicao_imediata(id_tarefa):
    """Força o agendamento a voltar para o status Pendente com execução para o dia de hoje."""
    if "usuario_logado" not in session:
        return redirect(url_for("index"))
        
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                # Altera o status e redefine a próxima pesquisa para a data de hoje.
                # Dessa forma, o robô operacional vai capturar o registro na próxima rodada.
                cur.execute(
                    "UPDATE dados SET status = 'Pendente', proxima_pesquisa = CURRENT_DATE WHERE id = %s", 
                    (id_tarefa,)
                )
            conn.commit()
        flash("Agendamento redefinido com sucesso! O robô executará a atualização na próxima rodada.", "success")
    except Exception as e:
        print(f"❌ Erro ao forçar atualização da tarefa {id_tarefa}: {e}")
        flash("Não foi possível solicitar a atualização imediata do item.", "danger")
        
    return redirect(url_for("index"))

@app.route('/admin/suspender/<int:id>')
def suspender_usuario(id):
    if session.get("perfil_logado") != "ADMIN":
        return "Acesso Restrito.", 403
        
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                # 1. Busca o status atual do usuário (coluna 'ativo')
                cur.execute("SELECT ativo FROM usuario WHERE id = %s", (id,))
                res = cur.fetchone()
                
                if res is not None:
                    status_atual = res[0]
                    # Se for 1 (Ativo), muda para 0. Se for 0 (Inativo), muda para 1.
                    novo_status = 0 if status_atual == 1 else 1
                    
                    # 2. Atualiza o banco com o novo status invertido
                    cur.execute("UPDATE usuario SET ativo = %s WHERE id = %s", (novo_status, id))
                    
                    if novo_status == 0:
                        flash("Operador suspenso com sucesso!", "success")
                    else:
                        flash("Operador reativado com sucesso!", "success")
            conn.commit()
            
    except Exception as e:
        print(f"❌ Erro ao alternar status do usuário ID {id}: {e}")
        flash("Não foi possível alterar o status do operador.", "danger")
        
    return redirect(url_for('admin_usuarios'))

@app.route("/admin/deletar_usuario/<int:id>")
def deletar_usuario(id):
    if session.get("perfil_logado") != "ADMIN":
        return "Acesso Restrito.", 403
        
    try:
        with GerenciadorConexao() as conn:
            with conn.cursor() as cur:
                # Remove o operador da tabela 'usuario' baseado no ID recebido
                cur.execute("DELETE FROM usuario WHERE id = %s", (id,))
            conn.commit()
        flash("Operador removido com sucesso da base física!", "success")
    except Exception as e:
        print(f"❌ Erro ao deletar o usuário ID {id}: {e}")
        flash("Não foi possível excluir este operador do sistema.", "danger")
        
    return redirect(url_for("admin_usuarios"))


if __name__ == "__main__":
    # O parâmetro debug=True desativa o cache interno do Flask e força a leitura do novo HTML
    app.run(host="0.0.0.0", port=5000, debug=True)
