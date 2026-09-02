import os
import psycopg2  # 🌟 Trocado: oracledb por psycopg2
from psycopg2 import extras  # Auxiliar para ler colunas por nome
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "chave_secreta_aqui"

# 🌟 CONFIGURAÇÕES DA SUA NOVA BASE POSTGRESQL NA HOSTINGER
DB_HOST = "179.199.138.119"
DB_NAME = "Minhabase"
DB_USER = "ramonn"
DB_PASS = "Synk2709@VPS"
DB_PORT = "5432"

# 🌟 FUNÇÃO QUE CRIA A ESTRUTURA DO ZERO ABSOLUTO NO POSTGRESQL
def inicializar_banco_do_zero():
    try:
        with psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS, port=DB_PORT) as conn:
            with conn.cursor() as cur:
                # 1. Tabela de Cidades
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS cidades_sistema (
                        id SERIAL PRIMARY KEY,
                        cidade VARCHAR(100) NOT NULL UNIQUE
                    );
                """)
                # Insere algumas cidades padrão se a tabela estiver vazia
                cur.execute("SELECT COUNT(*) FROM cidades_sistema;")
                if cur.fetchone()[0] == 0:
                    cidades_padrao = [("Salvador",), ("Feira de Santana",), ("Porto Seguro",)]
                    cur.executemany("INSERT INTO cidades_sistema (cidade) VALUES (%s);", cidades_padrao)

                # 2. Tabela de Usuários
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
                # Garante que o usuário admin exista inicialmente (senha padrão: admin123)
                cur.execute("SELECT COUNT(*) FROM usuario WHERE LOWER(nome) = 'admin';")
                if cur.fetchone()[0] == 0:
                    senha_admin = generate_password_hash("admin123")
                    cur.execute("""
                        INSERT INTO usuario (nome, senha, limite_maximo, primeiro_acesso, ativo, perfil)
                        VALUES ('admin', %s, 9999, 0, 1, 'ADMIN');
                    """, (senha_admin,))

                # 3. Tabela Principal de Dados
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

                # 4. Tabela de Concorrentes (Relacionada à tabela dados)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS dados_concorrentes (
                        id SERIAL PRIMARY KEY,
                        dados_id INTEGER REFERENCES dados(id) ON DELETE CASCADE,
                        ean_concorrente VARCHAR(20) NOT NULL
                    );
                """)
            conn.commit()
            print("🚀 Estrutura verificada/criada com sucesso no PostgreSQL!")
    except Exception as e:
        print(f"⚠️ Erro crítico ao criar estrutura inicial: {e}")

# Executa a criação assim que o app inicia
inicializar_banco_do_zero()

# 🌟 FUNÇÃO DE CONEXÃO ATUALIZADA PARA POSTGRESQL
def obter_conn():
    return psycopg2.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
        port=DB_PORT
    )

def ean_valido(ean):
    ean_limpo = str(ean).strip()
    return ean_limpo.isdigit() and len(ean_limpo) in [8, 13]  # 🌟 Correção realizada aqui

def buscar_cidades_banco():
    cidades = []
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT cidade FROM cidades_sistema ORDER BY cidade ASC')
                cidades = [str(row[0]).strip() for row in cur.fetchall() if row and row[0]]
    except Exception as e:
        print(f"Erro ao buscar cidades: {e}")
        cidades = ["Salvador (Reserva)", "Feira de Santana (Reserva)", "Porto Seguro (Reserva)"]
    return cidades

@app.route("/")
@app.route("/cadastro")
def index():
    if "usuario_logado" not in session: 
        return render_template("index.html", tela="login")
    
    usr = session["usuario_logado"]
    perf = session.get("perfil_logado")
    busca = request.args.get("q", "").strip().lower()
    f_st = request.args.get("status", "").strip().lower()
    
    tarefas = []
    met = {"total": 0, "pendentes": 0, "andamento": 0, "concluidas": 0}
    lista_cidades = buscar_cidades_banco()
    
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                wh = "WHERE 1=1" if perf == "ADMIN" else "WHERE d.usuario_dono = %s"
                query = f"""
                    SELECT d.id, d.codigo_barras, d.cidade, d.status, 
                           TO_CHAR(d.proxima_pesquisa,'DD/MM/YYYY') as prox_pesquisa_format, d.dias, 
                           (SELECT string_agg(c.ean_concorrente, ', ' ORDER BY c.id) 
                            FROM dados_concorrentes c WHERE c.dados_id = d.id) as concorrentes, 
                           d.proxima_pesquisa, TO_CHAR(d.data_cadastro,'DD/MM/YYYY HH24:MI') as cadastro_format
                    FROM dados d {wh} 
                    ORDER BY d.id DESC 
                    LIMIT 100
                """
                cur.execute(query, () if perf == "ADMIN" else (usr,))
                hj = datetime.now().date()
                
                for r in cur.fetchall():
                    t_id, t_cb, t_cid, t_st = r[0], r[1], r[2], r[3] or "Pendente"
                    t_pr_s, t_di, e_s, t_pr_r, t_cd = r[4], r[5], r[6] or "", r[7], r[8]  # 🌟 Evita erro de split em nulos
                    
                    le = [e.strip() for e in e_s.split(",") if e.strip()] if e_s else []

                    t_pr_r_date = t_pr_r.date() if isinstance(t_pr_r, datetime) else t_pr_r
                    dr = max(0, (t_pr_r_date - hj).days) if t_pr_r else t_di

                    if t_di == 9999:
                        t_re = 9999
                        if t_st == "Pendente": t_st = "Em Andamento"
                    else:
                        if t_di > 1 and t_st.strip().lower() == "pendente": 
                            t_re = 1
                        elif t_di > 1:
                            t_st = "Em Andamento"
                            t_re = max(0, t_di - max(0, t_di - dr))
                            if t_re == 0: t_st = "Concluída"
                        else:
                            t_re = dr
                            if t_re == 0: t_st = "Concluída"
                    
                    met["total"] += 1
                    if t_st == "Pendente": met["pendentes"] += 1
                    elif t_st == "Em Andamento": met["andamento"] += 1
                    elif t_st == "Concluída": met["concluidas"] += 1
                    
                    if busca and (busca not in str(t_cb).lower() and busca not in str(t_cid).lower()): continue
                    if f_st and f_st != t_st.lower(): continue
                    
                    tarefas.append([t_id, t_cb, t_cid, t_st, t_pr_s, t_re, t_di, le, t_cd, e_s])
                    
    except Exception as e: 
        print(f"⚠️ Falha de execução na rota principal: {e}")
        flash("Sincronização parcial com o banco de dados. Recarregue a página.", "danger")
        
    return render_template("index.html", tela="painel", cidades_opcoes=lista_cidades, tarefas=tarefas[:50], metrics=met, q=busca, status_sel=f_st)


@app.route("/cadastrar", methods=["POST"])
def cadastrar_requisicao():
    if "usuario_logado" not in session: 
        return redirect(url_for('index'))
    
    usr_dono = session["usuario_logado"]
    
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT limite_maximo FROM usuario WHERE LOWER(nome) = %s', (usr_dono.lower(),))
                res_user = cur.fetchone()
                limite_maximo = res_user[0] if res_user else 100
                
                cur.execute('SELECT COUNT(*) FROM dados WHERE usuario_dono = %s', (usr_dono,))
                res_count = cur.fetchone()
                total_atual = res_count[0] if res_count else 0
                
                if total_atual >= limite_maximo:
                    flash(f"⚠️ Limite atingido! Seu usuário permite apenas {limite_maximo} requisições.", "danger")
                    return redirect(url_for("index"))
    except Exception as e:
        flash(f"Erro ao validar limites no banco: {e}", "danger")
        return redirect(url_for("index"))

    cb = request.form.get("codigo_barras", "").strip()
    cid = request.form.get("cidade", "Não Informada").strip()
    dias = request.form.get("dias_pesquisa")
    
    if not ean_valido(cb):
        flash("O Código de Barras (EAN) principal é inválido! Deve conter 8 ou 13 dígitos numéricos.", "danger")
        return redirect(url_for("index"))
        
    dn = int(dias) if dias and dias.isdigit() else 9999
    eans_brutos = request.form.getlist("eans_concorrentes[]")
    eans = [e.strip() for e in eans_brutos if ean_valido(e)]
   
    # ... continuação da rota /cadastrar ...
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                px = datetime.now() + timedelta(days=(365*10 if dn == 9999 else dn))
                
                cur.execute("""
                    INSERT INTO dados (codigo_barras, cidade, status, proxima_pesquisa, dias, data_cadastro, usuario_dono)
                    VALUES (%s, %s, 'Pendente', %s, %s, CURRENT_TIMESTAMP, %s) RETURNING id
                """, (cb, cid, px.date(), dn, usr_dono))
                
                # Resgata o ID gerado na tupla de retorno do PostgreSQL
                n_id = cur.fetchone()[0]
                
                if eans and n_id:
                    cur.executemany(
                        "INSERT INTO dados_concorrentes (dados_id, ean_concorrente) VALUES (%s, %s)",
                        [(n_id, e) for e in eans]
                    )
            conn.commit()
            flash("Requisição cadastrada com sucesso!", "success")
    except Exception as e:
        flash(f"Erro ao salvar no banco: {e}", "danger")
        
    return redirect(url_for("index"))


@app.route('/login', methods=['POST'])
def realizar_login():
    u = request.form.get('usuario', '').strip().lower()
    s = request.form.get('senha', '').strip()
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT nome, senha, ativo, primeiro_acesso FROM usuario WHERE LOWER(nome) = %s", (u,))
                res = cur.fetchone()
                
        if res:
            nome_banco, senha_banco, ativo_banco, primeiro_acesso_banco = res[0], res[1], res[2], res[3]
            
            if ativo_banco == 0:
                flash("Sua conta está Bloqueada! Contate o administrador.", "danger")
                return redirect(url_for('index'))
                
            if s == senha_banco or (senha_banco.startswith('scrypt:') and check_password_hash(senha_banco, s)):
                session['usuario_logado'] = str(nome_banco)
                session['perfil_logado'] = "ADMIN" if str(nome_banco).lower() == "admin" else "USER"
                
                if primeiro_acesso_banco == 1:
                    session['forcar_troca_senha'] = True
                else:
                    session['forcar_troca_senha'] = False
                    
                return redirect(url_for('index'))
                
        flash("Usuário ou senha incorretos!", "danger")
    except Exception as e:
        flash(f"Erro de autenticação: {e}", "danger")
        
    return redirect(url_for('index'))


@app.route("/alterar_senha_primeiro_acesso", methods=["POST"])
def alterar_senha_primeiro_acesso():
    if "usuario_logado" not in session:
        return redirect(url_for('index'))
        
    usuario = session["usuario_logado"]
    nova_senha = request.form.get("nova_senha", "").strip()
    confirmar_senha = request.form.get("confirmar_senha", "").strip()
    
    if not nova_senha or nova_senha != confirmar_senha:
        flash("Senhas inválidas ou não coincidem!", "danger")
        return redirect(url_for('index'))
        
    senha_criptografada = generate_password_hash(nova_senha)
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE usuario
                    SET senha = %s, primeiro_acesso = 0
                    WHERE LOWER(nome) = %s
                """, (senha_criptografada, usuario.lower()))
            conn.commit()
            
        session['forcar_troca_senha'] = False
        flash("Sua nova senha foi gravada com sucesso! Bem-vindo.", "success")
    except Exception as e:
        flash(f"Erro ao redefinir sua senha: {e}", "danger")
        
    return redirect(url_for('index'))


@app.route("/editar", methods=["POST"])
def editar_requisicao():
    if "usuario_logado" not in session:
        return redirect(url_for('index'))
        
    id_a = request.form.get("id")
    cb = request.form.get("codigo_barras", "").strip()
    cid = request.form.get("cidade")
    dias = request.form.get("dias_pesquisa")
    
    if not ean_valido(cb):
        flash("Falha ao salvar: O Código de barras editado é inválido.", "danger")
        return redirect(url_for("index"))
        
    dn = int(dias) if dias and dias.isdigit() else 9999
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE dados
                    SET codigo_barras = %s, cidade = %s, dias = %s
                    WHERE id = %s
                """, (cb, cid, dn, id_a))
            conn.commit()
        flash("Registro atualizado com sucesso!", "success")
    except Exception as e:
        flash(f"Erro ao atualizar requisição: {e}", "danger")
        
    return redirect(url_for("index"))


@app.route("/admin/usuarios")
def admin_usuarios():
    if session.get("perfil_logado") != "ADMIN":
        return "Acesso Restrito.", 403
        
    usuarios = []
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, nome, limite_maximo, ativo FROM usuario ORDER BY id DESC")
                for row in cur.fetchall():
                    usuarios.append({
                        "ID": row[0],
                        "NOME": row[1],
                        "LIMITE_MAXIMO": row[2],
                        "ATIVO": row[3]
                    })
    except Exception as e:
        flash(f"Erro ao ler banco de dados: {e}", "danger")
        
    return render_template("adm_usuarios.html", usuarios=usuarios)


@app.route("/admin/usuarios/cadastrar", methods=["POST"])
def admin_cadastrar():
    if session.get("perfil_logado") != "ADMIN":
        return redirect(url_for('index'))
        
    u = request.form.get("usuario").strip().lower()
    s = request.form.get("senha").strip()
    limite = request.form.get("limite_maximo")
    perf_escolhido = request.form.get("perfil", "USER")
    limite_num = int(limite) if limite and limite.isdigit() else 100
    
    senha_criptografada = generate_password_hash(s)
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO usuario (nome, senha, limite_maximo, primeiro_acesso, ativo, perfil)
                    VALUES (%s, %s, %s, 1, 1, %s)
                """, (u, senha_criptografada, limite_num, perf_escolhido))
            conn.commit()
        flash(f"Usuário {u} gravado com limite de {limite_num} requisições!", "success")
    except Exception as e:
        flash(f"Erro ao salvar no PostgreSQL: {e}", "danger")
        
    return redirect(url_for("admin_usuarios"))


@app.route("/admin/usuarios/bloquear/<int:id_alvo>")
def admin_bloquear(id_alvo):
    if session.get("perfil_logado") != "ADMIN":
        return redirect(url_for('index'))
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ativo, nome FROM usuario WHERE id = %s", (id_alvo,))
                res = cur.fetchone()
                if res:
                    ativo_atual, nome_usuario = res[0], res[1]
                    
                    if nome_usuario.lower() == "admin":
                        flash("A conta master 'admin' não pode ser desativada!", "warning")
                        return redirect(url_for("admin_usuarios"))
                        
                    novo_status = 0 if ativo_atual == 1 else 1
                    cur.execute("UPDATE usuario SET ativo = %s WHERE id = %s", (novo_status, id_alvo))
                    
                    msg = "Bloqueado" if novo_status == 0 else "Ativado"
                    flash(f"Usuário {nome_usuario} foi {msg} com sucesso!", "success")
            conn.commit()
    except Exception as e:
        flash(f"Erro ao alterar status do usuário: {e}", "danger")
        
    return redirect(url_for("admin_usuarios"))


@app.route("/admin/usuarios/deletar/<int:id_alvo>")
def admin_deletar(id_alvo):
    if session.get("perfil_logado") != "ADMIN":
        return redirect(url_for('index'))
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM usuario WHERE id = %s AND LOWER(nome) != 'admin'", (id_alvo,))
            conn.commit()
        flash("Usuário removido com sucesso!", "success")
    except Exception as e:
        flash(f"Erro ao deletar: {e}", "danger")
        
    return redirect(url_for("admin_usuarios"))


@app.route("/admin/usuarios/editar", methods=["POST"])
def admin_editar():
    if session.get("perfil_logado") != "ADMIN":
        return redirect(url_for('index'))
        
    id_alvo = request.form.get("id")
    novo_nome = request.form.get("usuario").strip().lower()
    nova_senha = request.form.get("senha", "").strip()
    limite = request.form.get("limite_maximo")
    limite_num = int(limite) if limite and limite.isdigit() else 100
    
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                if nova_senha:
                    senha_criptografada = generate_password_hash(nova_senha)
                    cur.execute("""
                        UPDATE usuario
                        SET nome = %s, senha = %s, limite_maximo = %s
                        WHERE id = %s AND LOWER(nome) != 'admin'
                    """, (novo_nome, senha_criptografada, limite_num, id_alvo))
                else:
                    cur.execute("""
                        UPDATE usuario
                        SET nome = %s, limite_maximo = %s
                        WHERE id = %s AND LOWER(nome) != 'admin'
                    """, (novo_nome, limite_num, id_alvo))
            conn.commit()
        flash("Dados do operador atualizados com sucesso!", "success")
    except Exception as e:
        flash(f"Erro ao editar no PostgreSQL: {e}", "danger")  # 🌟 Agora o Python vê o recuo correto aqui
        
    return redirect(url_for("admin_usuarios"))


@app.route("/deletar_bloco", methods=["POST"])
def deletar_bloco_requisicao():
    if "usuario_logado" not in session:
        return redirect(url_for('index'))
        
    ids_selecionados = request.form.getlist("usuarios_deletar[]")
    if not ids_selecionados:
        flash("Nenhuma requisição foi selecionada para exclusão!", "warning")
        return redirect(url_for("index"))
        
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany("DELETE FROM dados_concorrentes WHERE dados_id = %s", [(idx,) for idx in ids_selecionados])
                cur.executemany("DELETE FROM dados WHERE id = %s", [(idx,) for idx in ids_selecionados])
            conn.commit()
        flash(f"{len(ids_selecionados)} requisições excluídas com sucesso em bloco!", "success")
    except Exception as e:
        flash(f"Erro ao remover em bloco: {e}", "danger")
        
    return redirect(url_for("index"))


@app.route("/deletar_individual/<int:id_alvo>")
def deletar_requisicao(id_alvo):
    if "usuario_logado" not in session:
        return redirect(url_for('index'))
        
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM dados_concorrentes WHERE dados_id = %s", (id_alvo,))
                cur.execute("DELETE FROM dados WHERE id = %s", (id_alvo,))
            conn.commit()
        flash("Requisição removida com sucesso!", "success")
    except Exception as e:
        flash(f"Erro ao deletar requisição: {e}", "danger")
        
    return redirect(url_for("index"))

# --- LOGOUT ---
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


# --- INICIALIZAÇÃO ---
# 🌟 CORREÇÃO: Alterado para a nomenclatura padrão do Python com sublinhados duplos
if __name__ == "__main__":
    # O Render define uma variável chamada PORT automaticamente, precisamos escutá-la
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
