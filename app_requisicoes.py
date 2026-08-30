import os, oracledb, pandas as pd
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "chave_secreta_aqui"

# CONFIGURAÇÕES DO BANCO DE DADOS
DB_USER, DB_PASSWORD, ALIAS_CONEXAO, SENHA_WALLET = "ADMIN", "Bd@123@456@789", "bdprice01_high", "Bd@123@456@789"

# 🌟 CORREÇÃO PARA A NUVEM: Caminho relativo que funciona tanto no seu PC quanto no Render
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CAMINHO_WALLET = os.path.join(BASE_DIR, "wallet")

try: 
    pool_oracle = oracledb.create_pool(
        user=DB_USER, password=DB_PASSWORD, dsn=ALIAS_CONEXAO, 
        wallet_location=CAMINHO_WALLET, config_dir=CAMINHO_WALLET, 
        wallet_password=SENHA_WALLET, 
        min=1,           
        max=8,           
        increment=1,
        getmode=oracledb.POOL_GETMODE_NOWAIT 
    )
except Exception as e: 
    print(f"⚠️ Erro ao criar o Pool do Oracle: {e}")
    pool_oracle = None

def obter_conn():
    if not pool_oracle: raise oracledb.InterfaceError("Pool Oracle está offline.")
    return pool_oracle.acquire()

def ean_valido(ean):
    ean_limpo = str(ean).strip()
    return ean_limpo.isdigit() and len(ean_limpo) in [8, 13]

def buscar_cidades_banco():
    cidades = []
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT "cidade" FROM cidades_sistema ORDER BY "cidade" ASC')
                cidades = [str(row[0]).strip() for row in cur.fetchall() if row and row[0]]
    except Exception as e:
        try:
            with obter_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT cidade FROM cidades_sistema ORDER BY cidade ASC')
                    cidades = [str(row[0]).strip() for row in cur.fetchall() if row and row[0]]
        except Exception as e_reserva:
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
                wh = "WHERE 1=1" if perf == "ADMIN" else "WHERE d.usuario_dono = :u_d"
                query = f"""
                    SELECT d.id, d.codigo_barras, d.cidade, d.status, 
                           TO_CHAR(d.proxima_pesquisa,'DD/MM/YYYY'), d.dias, 
                           (SELECT LISTAGG(c.ean_concorrente,', ') WITHIN GROUP (ORDER BY c.id) 
                            FROM dados_concorrentes c WHERE c.dados_id = d.id), 
                           d.proxima_pesquisa, TO_CHAR(d.data_cadastro,'DD/MM/YYYY HH24:MI') 
                    FROM dados d {wh} 
                    ORDER BY d.id DESC 
                    FETCH FIRST 100 ROWS ONLY
                """
                cur.execute(query, {} if perf == "ADMIN" else {"u_d": usr})
                hj = datetime.now().date()
                
                for r in cur.fetchall():
                    t_id, t_cb, t_cid, t_st = r[0], r[1], r[2], r[3] or "Pendente"
                    t_pr_s, t_di, e_s, t_pr_r, t_cd = r[4], r[5], r[6] or "", r[7], r[8]
                    
                    le = [e.strip() for e in e_s.split(",") if e.strip()] if e_s else []
                    dr = max(0, ((t_pr_r.date() if isinstance(t_pr_r, datetime) else t_pr_r) - hj).days) if t_pr_r else t_di
                    
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
        flash("Sincronização parcial com o banco Oracle Cloud. Recarregue a página.", "danger")
        
    return render_template("index.html", tela="painel", cidades_opcoes=lista_cidades, tarefas=tarefas[:50], metrics=met, q=busca, status_sel=f_st)
@app.route("/cadastrar", methods=["POST"])
def cadastrar_requisicao():
    if "usuario_logado" not in session: 
        return redirect(url_for('index'))
    
    usr_dono = session["usuario_logado"]
    
    # --- NOVO BLOCO DE VALIDAÇÃO DE LIMITE ---
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                # 1. Busca o limite máximo do usuário atual
                cur.execute('SELECT limite_maximo FROM usuario WHERE LOWER(nome) = :u', {"u": usr_dono.lower()})
                res_user = cur.fetchone()
                limite_maximo = res_user[0] if res_user else 100 # Se não achar, assume 100 por segurança
                
                # 2. Conta quantas requisições ativas este usuário já possui no sistema
                cur.execute('SELECT COUNT(*) FROM dados WHERE usuario_dono = :u', {"u": usr_dono})
                res_count = cur.fetchone()
                total_atual = res_count[0] if res_count else 0
                
                # 3. Se ele já atingiu ou passou do limite, barra o cadastro
                if total_atual >= limite_maximo:
                    flash(f"⚠️ Limite atingido! Seu usuário permite apenas {limite_maximo} requisições no sistema.", "danger")
                    return redirect(url_for("index"))
    except Exception as e:
        flash(f"Erro ao validar limites no banco: {e}", "danger")
        return redirect(url_for("index"))
    # --- FIM DO BLOCO DE VALIDAÇÃO ---

    # Daqui para baixo continua o seu código original de inserção
    cb = request.form.get("codigo_barras", "").strip()
    cid = request.form.get("cidade", "Não Informada").strip()
    dias = request.form.get("dias_pesquisa")
    
    if not ean_valido(cb):
        flash("O Código de Barras (EAN) principal é inválido! Deve conter 8 ou 13 dígitos numéricos.", "danger")
        return redirect(url_for("index"))
        
    dn = int(dias) if dias and dias.isdigit() else 9999
    eans_brutos = request.form.getlist("eans_concorrentes[]")
    eans = [e.strip() for e in eans_brutos if ean_valido(e)]
    
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                px = "15/08/2026" if dn == 9999 else (datetime.now() + timedelta(days=dn)).strftime('%d/%m/%Y')
                cur.execute("INSERT INTO dados (codigo_barras, cidade, status, proxima_pesquisa, dias, data_cadastro, usuario_dono) VALUES (:cb, :cid, 'Pendente', TO_DATE(:px, 'DD/MM/YYYY'), :dias, SYSDATE, :dono)", {"cb": cb, "cid": cid, "px": px, "dias": dn, "dono": usr_dono})
                
                cur.execute("SELECT MAX(id) FROM dados")
                r_id = cur.fetchone()
                n_id = r_id[0] if r_id else None
                
                if eans and n_id: 
                    cur.executemany("INSERT INTO dados_concorrentes (dados_id, ean_concorrente) VALUES (:dados_id, :ean)", [{"dados_id": n_id, "ean": e} for e in eans])
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
                cur.execute("SELECT nome, senha, ativo, primeiro_acesso FROM usuario WHERE LOWER(nome) = :u", {"u": u})
                res = cur.fetchone()
        if res:
            nome_banco, senha_banco, ativo_banco, primeiro_acesso_banco = res[0], res[1], res[2], res[3]
            
            if ativo_banco == 0:
                flash("Sua conta está Bloqueada! Contate o administrador.", "danger")
                return redirect(url_for('index'))
                
            # Verifica a senha digitada contra o hash ou o texto plano do Oracle Cloud
            if s == senha_banco or (senha_banco.startswith('scrypt:') and check_password_hash(senha_banco, s)):
                session['usuario_logado'] = str(nome_banco)
                session['perfil_logado'] = "ADMIN" if str(nome_banco).lower() == "admin" else "USER"
                
                # Controle do modal flutuante de redefinição obrigatória
                if primeiro_acesso_banco == 1:
                    session['forcar_troca_senha'] = True
                else:
                    session['forcar_troca_senha'] = False
                    
                return redirect(url_for('index'))
        flash("Usuário ou senha incorretos!", "danger")
    except Exception as e:
        flash(f"Erro de autenticação: {e}", "danger")
    return redirect(url_for('index'))



# 🌟 CORREÇÃO REALIZADA AQUI: Adicionado tratamento para atualizar a sessão após a troca
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
        
    # Gera a criptografia correta da nova credencial
    senha_criptografada = generate_password_hash(nova_senha)
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                # Modifica os campos e marca o primeiro_acesso como concluído (0)
                # Continuação da rota de alteração de senha
                cur.execute("""
                    UPDATE usuario 
                    SET senha = :s, primeiro_acesso = 0 
                    WHERE LOWER(nome) = :u
                """, {"s": senha_criptografada, "u": usuario.lower()})
            conn.commit()
            
        # Desliga o gatilho da janela flutuante para liberar a navegação do painel
        session['forcar_troca_senha'] = False
        flash("Sua nova senha foi gravada com sucesso! Bem-vindo.", "success")
    except Exception as e:
        flash(f"Erro ao redefinir sua senha: {e}", "danger")
        
    return redirect(url_for('index'))

# --- ADICIONE ESTE BLOCO EXATO PARA CORRIGIR O ERRO DE COMPILAÇÃO ---
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
                    SET codigo_barras = :cb, cidade = :cid, dias = :dias 
                    WHERE id = :id
                """, {"cb": cb, "cid": cid, "dias": dn, "id": id_a})
            conn.commit()
        flash("Registro atualizado com sucesso!", "success")
    except Exception as e: 
        flash(f"Erro ao atualizar requisição: {e}", "danger")
        
    return redirect(url_for("index"))



# --- LISTAGEM DE USUÁRIOS ---
@app.route("/admin/usuarios")
def admin_usuarios():
    if session.get("perfil_logado") != "ADMIN": 
        return "Acesso Restrito.", 403
        
    usuarios = []
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ID, NOME, LIMITE_MAXIMO, ATIVO FROM USUARIO ORDER BY ID DESC")
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


# --- CADASTRO DE USUÁRIO ---
@app.route("/admin/usuarios/cadastrar", methods=["POST"])
def admin_cadastrar():
    if session.get("perfil_logado") != "ADMIN":
        return redirect(url_for('index'))
        
    u = request.form.get("usuario").strip().lower()
    s = request.form.get("senha").strip()
    limite = request.form.get("limite_maximo")
    perf_escolhido = request.form.get("perfil", "USER") # <-- CAPTURA O PERFIL DO FORMULÁRIO
    limite_num = int(limite) if limite and limite.isdigit() else 100
    
    # Novos usuários salvos com hash e primeiro_acesso marcado como 1 obrigatoriamente
    senha_criptografada = generate_password_hash(s)
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO usuario (nome, senha, limite_maximo, primeiro_acesso, ativo,perfil)
                    VALUES (:u, :s, :lim, 1, 1)
                """, {"u": u, "s": senha_criptografada, "lim": limite_num,"perf": perf_escolhido})
            conn.commit()
        flash(f"Usuário {u} gravado com limite de {limite_num} requisições!", "success")
    except Exception as e:
        flash(f"Erro ao salvar no Oracle: {e}", "danger")
        
    return redirect(url_for("admin_usuarios"))


# --- BLOQUEAR / DESBLOQUEAR USUÁRIO ---
# 🌟 CORREÇÃO: Adicionado <int:id_alvo> na rota
@app.route("/admin/usuarios/bloquear/<int:id_alvo>")
def admin_bloquear(id_alvo):
    if session.get("perfil_logado") != "ADMIN":
        return redirect(url_for('index'))
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ativo, nome FROM usuario WHERE id = :id", {"id": id_alvo})
                res = cur.fetchone()
                if res:
                    ativo_atual, nome_usuario = res[0], res[1]
                    
                    if nome_usuario.lower() == "admin":
                        flash("A conta master 'admin' não pode ser desativada!", "warning")
                        return redirect(url_for("admin_usuarios"))
                        
                    novo_status = 0 if ativo_atual == 1 else 1
                    cur.execute("UPDATE usuario SET ativo = :ativo WHERE id = :id", {"ativo": novo_status, "id": id_alvo})
                    
                    msg = "Bloqueado" if novo_status == 0 else "Ativado"
                    flash(f"Usuário {nome_usuario} foi {msg} com sucesso!", "success")
            conn.commit()
    except Exception as e:
        flash(f"Erro ao alterar status do usuário: {e}", "danger")
        
    return redirect(url_for("admin_usuarios"))


# --- EXCLUSÃO DE USUÁRIO ---
# 🌟 CORREÇÃO: Adicionado <int:id_alvo> na rota
@app.route("/admin/usuarios/deletar/<int:id_alvo>")
def admin_deletar(id_alvo):
    if session.get("perfil_logado") != "ADMIN":
        return redirect(url_for('index'))
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM usuario WHERE id = :id AND LOWER(nome) != 'admin'", {"id": id_alvo})
            conn.commit()
        flash("Usuário removido com sucesso!", "success")
    except Exception as e:
        flash(f"Erro ao deletar: {e}", "danger")
        
    return redirect(url_for("admin_usuarios"))


# --- EDIÇÃO DE USUÁRIO ---
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
                        SET nome = :nome, senha = :senha, limite_maximo = :lim
                        WHERE id = :id AND LOWER(nome) != 'admin'
                    """, {"nome": novo_nome, "senha": senha_criptografada, "lim": limite_num, "id": id_alvo})
                else:
                    cur.execute("""
                        UPDATE usuario
                        SET nome = :nome, limite_maximo = :lim
                        WHERE id = :id AND LOWER(nome) != 'admin'
                    """, {"nome": novo_nome, "lim": limite_num, "id": id_alvo})
            conn.commit()
        flash("Dados do operador atualizados com sucesso!", "success")
    except Exception as e:
        flash(f"Erro ao editar no Oracle: {e}", "danger")
        
    return redirect(url_for("admin_usuarios"))


# --- EXCLUSÃO EM BLOCO DE REQUISIÇÕES ---
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
                cur.executemany("DELETE FROM dados_concorrentes WHERE dados_id = :id", [{"id": idx} for idx in ids_selecionados])
                cur.executemany("DELETE FROM dados WHERE id = :id", [{"id": idx} for idx in ids_selecionados])
            conn.commit()
        flash(f"{len(ids_selecionados)} requisições excluídas com sucesso em bloco!", "success")
    except Exception as e:
        flash(f"Erro ao remover em bloco no Oracle: {e}", "danger")
        
    return redirect(url_for("index"))
@app.route("/deletar_individual/<int:id_alvo>")
def deletar_requisicao(id_alvo):
    if "usuario_logado" not in session: 
        return redirect(url_for('index'))
    try:
        with obter_conn() as conn:
            with conn.cursor() as cur:
                # Remove primeiro os concorrentes para não quebrar a chave estrangeira
                cur.execute("DELETE FROM dados_concorrentes WHERE dados_id = :id", {"id": id_alvo})
                cur.execute("DELETE FROM dados WHERE id = :id", {"id": id_alvo})
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
