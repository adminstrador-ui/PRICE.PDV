# ==============================================================================
# SEÇÃO 1: DIRETRIZES DE CREDENCIAIS (PROXIES, BANCO E CAPTCHA)
# ==============================================================================
import os
import time
import zipfile
import warnings
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
import json
from twocaptcha import TwoCaptcha

# Supressão de Avisos de Biblioteca
warnings.filterwarnings("ignore", category=UserWarning)

# Token do provedor de resolução de captcha automatizado
API_KEY_CAPTCHA = "c599041e71136ea2aaa7165bff216429"

# Dados ISP obtidos diretamente do seu painel Bright Data
PROXY_HOST = "brd.superproxy.io"
PROXY_PORT = "44445"
PROXY_USER = "brd-customer-hl_35f154bd-zone-roboprice"
PROXY_PASS = "vrae85951ca9"

# Dados de conexão estruturais com o PostgreSQL (Hostinger)
DB_HOST = "179.199.138.119"
DB_PORT = "5432"
DB_NAME = "Minhabase"     
DB_USER = "ramonn"        
DB_PASS = "Synk2709@VPS"  

# Nome do arquivo unificado para backup incremental local
NOME_EXCEL_HISTORICO = "Historico_Graficos.xlsx"

# ==============================================================================
# SEÇÃO 2: CONTROLADORES DE PERSISTÊNCIA (POSTGRESQL / EXCEL)
# ==============================================================================
def executar_banco(q, d=None, ler=False):
    """Gerencia conexões e execuções no Banco de Dados PostgreSQL."""
    conexao_str = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASS}"
    with psycopg2.connect(conexao_str) as conn:
        if ler:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(q, d if d else [])
                return [dict(line) for line in cur.fetchall()]
        else:
            with conn.cursor() as cur:
                cur.execute(q, d if d else [])
            conn.commit()


def salvar_historico_grafico_banco_e_excel(lista_historico_grafico):
    """Insere os dados coletados do gráfico diretamente no PostgreSQL seguindo a estrutura real de colunas."""
    if not lista_historico_grafico:
        return
        
    conexao_str = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASS}"
    
    # 🚀 QUERY ALINHADA COM A IMAGEM DO SEU BANCO DE DADOS
    query = """
        INSERT INTO grafico_historico_precos (
            dados_id, codigo_barras, tipo_produto, 
            data_preco_historico, preco_medio, preco_minimo, data_coleta
        ) VALUES %s
    """
    
    valores = [
        (
            int(item["ID Pai (DADOS)"]),
            str(item["Código de Barras"])[:20],
            str(item["Tipo Produto"])[:20],  # Alinhado com o seu varchar(20)
            str(item["Data do Preço Histórico"])[:20],
            float(item["Preço Médio"] or 0) if item["Preço Médio"] is not None else 0.0,
            float(item["Preço Mínimo"] or 0) if item["Preço Mínimo"] is not None else 0.0,
            str(item["Data Coleta"])
        )
        for item in lista_historico_grafico
    ]
    
    try:
        with psycopg2.connect(conexao_str) as conn:
            with conn.cursor() as cur:
                execute_values(cur, query, valores)
            conn.commit()
        print(f"   💾 [POSTGRESQL] {len(lista_historico_grafico)} linhas salvas com sucesso em 'grafico_historico_precos'.")
    except Exception as e_db:
        print(f"   ❌ [ERRO BANCO GRÁFICO] Falha ao gravar na tabela: {e_db}")

    # 📊 Mantém o arquivo Excel com o mapeamento completo original (Backup)
    df_novos = pd.DataFrame(lista_historico_grafico)
    ordem_colunas = [
        "ID Pai (DADOS)", "Código de Barras", "Tipo Produto", 
        "Filtro Aplicado", "Nome do Estabelecimento", "CNPJ do Estabelecimento",
        "Data do Preço Histórico", "Preço Médio", "Preço Mínimo", "Data Coleta"
    ]
    df_novos = df_novos[[c for c in ordem_colunas if c in df_novos.columns]]

    if os.path.exists(NOME_EXCEL_HISTORICO):
        try:
            df_antigo = pd.read_excel(NOME_EXCEL_HISTORICO)
            df_consolidado = pd.concat([df_antigo, df_novos], ignore_index=True)
        except Exception:
            df_consolidado = df_novos
    else:
        df_consolidado = df_novos
    df_consolidado.to_excel(NOME_EXCEL_HISTORICO, index=False)

# ==============================================================================
# SEÇÃO 3: NAVEGAÇÃO E SISTEMA ANTIBLOQUEIO (2CAPTCHA)
# ==============================================================================
def configurar_proxy_rotativo(options, host, port, user, password):
    manifest_json = """
    {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Chrome Proxy",
        "permissions": ["proxy", "tabs", "unlimitedStorage", "storage", "<all_urls>", "webRequest", "webRequestBlocking"],
        "background": { "scripts": ["background.js"] },
        "minimum_chrome_version":"22.0.0"
    }
    """
    background_js = f"""
    var config = {{
        mode: "fixed_servers",
        rules: {{ singleProxy: {{ scheme: "http", host: "{host}", port: parseInt({port}) }}, bypassList: [] }}
    }};
    chrome.proxy.settings.set({{value: config, scope: "regular"}}, function({{}});
    chrome.webRequest.onAuthRequired.addListener(
        function(details) {{ return {{ authCredentials: {{ username: "{user}", password: "{password}" }} }}; }},
        {{urls: ["<all_urls>"]}}, ["blocking"]
    );
    """
    plugin_file = 'proxy_auth_plugin.zip'
    with zipfile.ZipFile(plugin_file, 'w') as zp:
        zp.writestr("manifest.json", manifest_json)
        zp.writestr("background.js", background_js)
    options.add_extension(plugin_file)
    return options


def verificar_e_resolver_captcha():
    time.sleep(2) 
    url_atual = nav.current_url
    captcha_presente = False
    if "/challenge/" in url_atual:
        captcha_presente = True
    else:
        try:
            elementos = nav.find_elements(By.CLASS_NAME, "g-recaptcha") + nav.find_elements(By.XPATH, "//iframe[contains(@src, 'recaptcha')]")
            if elementos and any(el.is_displayed() for el in elementos):
                captcha_presente = True
        except: pass

    if not captcha_presente: return

    print("[AVISO INTERVENÇÃO] Bloqueio por reCAPTCHA detectado! Acionando 2Captcha...")
    try:
        solver = TwoCaptcha(API_KEY_CAPTCHA)
        try: sitekey = nav.find_element(By.CLASS_NAME, "g-recaptcha").get_attribute("data-sitekey")
        except: sitekey = "6Lfj7mEaAAAAANgG-5n5f_x95Z_Z1_G_g3_g8g_g" 
        result = solver.recaptcha(sitekey=sitekey, url=url_atual)
        token_resposta = result['code']
        nav.execute_script(f'document.getElementById("g-recaptcha-response").innerHTML = "{token_resposta}";')
        try: nav.execute_script(f'document.getElementsByName("g-recaptcha-response").value = "{token_resposta}";')
        except: pass
        nav.find_element(By.XPATH, "//button[contains(text(), 'Enviar')] | //input[@type='submit'] | //button[@type='submit'] | //*[@class='btn btn-primary']").click()
        time.sleep(5) 
    except Exception as e_captcha: print(f"[ERRO CRÍTICO CAPTCHA] Falha na quebra: {e_captcha}")


def fechar_popup_anuncio():
    time.sleep(2)
    try:
        botoes_fechar = nav.find_elements(By.XPATH, "//button[@class='close'] | //*[contains(@class, 'modal')]//button[text()='×'] | //button[contains(@class, 'close-modal')]")
        for btn in botoes_fechar:
            if btn.is_displayed(): btn.click(); time.sleep(1); return
    except: pass


def clicar_elemento(xpath, timeout_sleep=1.5):
    time.sleep(timeout_sleep)
    wait.until(EC.element_to_be_clickable((By.XPATH, xpath))).click()


def forcar_rolagem_pagina():
    print("[ROBÔ] Forçando rolagem de página...")
    for i in range(1, 6):
        nav.execute_script(f"window.scrollTo(0, (document.body.scrollHeight / 5) * {i});")
        time.sleep(1.2)
    nav.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(1.5)


def abrir_grafico_historico(botao_da_linha):
    """Abre o histórico salvando o padrão (Bahia) e depois clica em Estabelecimento para capturar ambos."""
    try:
        # 🚀 CORREÇÃO 1: Adicionado [0] para referenciar corretamente o elemento no JavaScript
        nav.execute_script("arguments[0].scrollIntoView({block: 'center'});", botao_da_linha)
        time.sleep(0.3)
        
        actions = ActionChains(nav)
        actions.move_to_element(botao_da_linha).click().perform()
        time.sleep(2.5) # Bahia carrega por padrão

        verificar_e_resolver_captcha()

        # Clique forçado via JS para afastar erros de 'click intercepted' com a navbar do portal
        xpath_estabelecimento = "//input[@value='estabelecimento'] | //label[contains(., 'Estabelecimento')] | //span[contains(text(), 'Estabelecimento')]"
        try:
            radio_estabelecimento = nav.find_element(By.XPATH, xpath_estabelecimento)
            
            # 🚀 CORREÇÃO 2: Adicionado [0] no scroll do rádio botão
            nav.execute_script("arguments[0].scrollIntoView({block: 'center'});", radio_estabelecimento)
            time.sleep(0.2)
            
            # 🚀 CORREÇÃO 3: Adicionado [0] no clique do rádio botão
            nav.execute_script("arguments[0].click();", radio_estabelecimento)
            time.sleep(2.5) # Espera carregar o JSON do estabelecimento
        except Exception as e_estab:
            print(f"      [AVISO FILTRO] Falha ao alternar para Estabelecimento: {e_estab}")

        xpath_fechar = "//button[@class='close'] | //*[contains(@class, 'modal')]//button[text()='×'] | //button[contains(text(), 'Fechar')]"
        try: 
            # 🚀 CORREÇÃO 4: Adicionado [0] no clique de fechar o modal
            nav.execute_script("arguments[0].click();", nav.find_element(By.XPATH, xpath_fechar))
        except: 
            pass
        time.sleep(0.5)
    except Exception as e_modal: 
        print(f"      [ERRO MODAL] Falha geral na rotina gráfica: {e_modal}")


def fazer_pesquisa_ean(ean_codigo):
    try:
        campo_busca = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='fake-sbar'] | //input[@id='sbar']")))
        campo_busca.click()
        actions = ActionChains(nav)
        actions.double_click(campo_busca).click(campo_busca).perform()
        campo_busca.clear()
        campo_busca.send_keys(Keys.BACKSPACE * 20)
        campo_busca.send_keys(str(ean_codigo) + Keys.ENTER)
        time.sleep(2.5)
    except Exception:
        nav.get("https://precodahora.ba.gov.br"); time.sleep(4); fechar_popup_anuncio()
        campo_busca = wait.until(EC.presence_of_element_located((By.XPATH, "//*[@id='fake-sbar'] | //input[@id='sbar']")))
        campo_busca.click(); campo_busca.clear()
        campo_busca.send_keys(str(ean_codigo) + Keys.ENTER)
        time.sleep(2.5)

# ==============================================================================
# SEÇÃO 4: PROCESSADOR E DECODIFICADOR TEXTUAL DE GRÁFICOS
# ==============================================================================
def decodificar_dados_grafico(lista_api, ean_referencia, id_dados, tipo_produto_str):
    """Processa estritamente os pacotes contendo respostas de históricos gráficos."""
    h_rodada = []
    if not lista_api: return h_rodada
    for api_item in lista_api:
        url_atual = str(api_item.get('url', ''))
        ean_atual = api_item.get('ean', ean_referencia)
        api = api_item.get('json')
        if not api or 'historico' not in url_atual.lower(): continue
        try:
            if isinstance(api, str):
                try: api = json.loads(api)
                except: pass
            if isinstance(api, str):
                try:
                    if api.startswith('"') and api.endswith('"'): api = api[1:-1]
                    api = json.loads(api.replace('\\"', '"'))
                except: pass

            if isinstance(api, dict):
                resultado_bloco = api.get('resultado', {})
                if isinstance(resultado_bloco, dict):
                    lista_datas = resultado_bloco.get('data', [])
                    lista_medias = resultado_bloco.get('precoMedio', [])
                    lista_minimos = resultado_bloco.get('precoMinimo', [])
                    nome_loja = api.get('nomeEstablishment', api.get('nomeEstabelecimento', api.get('estabelecimento', '')))
                    cnpj_loja = api.get('cnpj', api.get('cnpjEstabelecimento', ''))
                    
                    if not nome_loja:
                        if "cnpj=" in url_atual.lower() or "id_estabelecimento" in url_atual.lower():
                            tipo_filtro = "ESTABELECIMENTO"; nome_loja = "Estabelecimento Mapeado"; cnpj_loja = "Verificar URL"
                        else: tipo_filtro = "BAHIA"; nome_loja = "Bahia (Geral)"; cnpj_loja = "Não Aplicável"
                    else: tipo_filtro = "ESTABELECIMENTO"

                    if lista_datas:
                        for idx in range(len(lista_datas)):
                            p_medio = lista_medias[idx] if idx < len(lista_medias) else None
                            p_minimo = lista_minimos[idx] if idx < len(lista_minimos) else None
                            h_rodada.append({
                                "ID Pai (DADOS)": id_dados, "Código de Barras": ean_atual,
                                "Tipo Produto": tipo_produto_str, "Filtro Aplicado": tipo_filtro,
                                "Nome do Estabelecimento": nome_loja, "CNPJ do Estabelecimento": str(cnpj_loja),
                                "Data do Preço Histórico": lista_datas[idx],
                                "Preço Médio": float(p_medio or 0), "Preço Mínimo": float(p_minimo or 0),
                                "Data Coleta": time.strftime("%Y-%m-%d %H:%M:%S")
                            })
                        print(f"   📊 [DECODIFICADOR] {tipo_filtro} decodificado para ({nome_loja}).")
        except Exception: pass
    return h_rodada

# ==============================================================================
# SEÇÃO 5: INICIALIZAÇÃO DA INSTÂNCIA DO CHROME WEBDRIVER (CDP NETWORK)
# ==============================================================================
# 🚀 FILTRAGEM INDEPENDENTE: Puxa apenas quem o robô de históricos ainda não processou
pesquisas = executar_banco("SELECT ID AS id_dados, CIDADE AS cidade, CODIGO_BARRAS FROM DADOS WHERE UPPER(status_historico) = 'PENDENTE' ORDER BY CIDADE", ler=True)
if not pesquisas:
    print("[AVISO] Nenhuma pendência encontrada na coluna 'status_historico'. Encerrando.")
    exit()

o = Options()
o.add_experimental_option("detach", True)
o.add_experimental_option("excludeSwitches", ["enable-automation"])
o.add_argument("--disable-blink-features=AutomationControlled")
o.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
o = configurar_proxy_rotativo(o, PROXY_HOST, PROXY_PORT, PROXY_USER, PROXY_PASS)

nav = webdriver.Chrome(options=o)

# Escuta CDP convertendo primeiro em Texto Puro contra escapes textuais do Sefaz BA
nav.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": """
(function() {
    if (!window.dados_precos) window.dados_precos = [];
    const interceptar = (url, textoResponse) => {
        if (url.toLowerCase().includes('historico')) {
            try { let obj = JSON.parse(textoResponse); if(obj) window.dados_precos.push({url: url, json: obj, ean: window.current_ean_target}); } catch(e){}
        }
    };
    let X = XMLHttpRequest.prototype, oOpen = X.open, oSend = X.send;
    X.open = function(m, u) { this._url = u; return oOpen.apply(this, arguments); };
    X.send = function() {
        this.addEventListener('load', function() { interceptar(this._url, this.responseText); });
        return oSend.apply(this, arguments);
    };
    let originalFetch = window.fetch;
    window.fetch = async function(...args) {
        let response = await originalFetch(...args), clone = response.clone();
        try { clone.text().then(textoBruto => { interceptar(args[0] || args, textoBruto); }); } catch(e){}
        return response;
    };
})();
"""})

nav.get("https://precodahora.ba.gov.br")
nav.maximize_window()
wait = WebDriverWait(nav, 15)

# ==============================================================================
# SEÇÃO 6: LAÇO CONTÍNUO ASSÍNCRONO EXCLUSIVO PARA HISTÓRICOS
# ==============================================================================
try:
    while True:
        # 🚀 CONSULTA FILTRADA DA FILA DO HISTÓRICO
        pesquisas = executar_banco("SELECT ID AS id_dados, CIDADE AS cidade, CODIGO_BARRAS FROM DADOS WHERE UPPER(status_historico) = 'PENDENTE' ORDER BY CIDADE, ID DESC", ler=True)
        if not pesquisas:
            print("[AVISO] Sem registros pendentes em 'status_historico'. Dormindo 3 min..."); time.sleep(180); continue

        df_lote = pd.DataFrame(pesquisas)
        for cidade_ativa in df_lote['cidade'].unique():
            print(f"\n📍 [ROBÔ HISTÓRICO] Localidade ativa: {cidade_ativa}")
            tarefas_da_cidade = df_lote[df_lote['cidade'] == cidade_ativa].to_dict('records')

            try:
                nav.get("https://precodahora.ba.gov.br"); time.sleep(4); fechar_popup_anuncio()
                for xpath in ["/html/body/header/div/div/nav/div/a/i", "//*[@id='sidebar-filtros-home']/div/div/div/button", "//*[@id='add-center']"]: clicar_elemento(xpath)
                
                input_cidade = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='modal-regions']//input[@type='text']")))
                input_cidade.click(); input_cidade.send_keys(cidade_ativa)
                try: wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='sugerir-municipios']//li[2]"))).click()
                except: wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='sugerir-municipios']/ul/li"))).click()
                clicar_elemento("//*[@id='aplicar']"); time.sleep(4)
            except Exception as e:
                print(f"❌ Erro de geolocalização em {cidade_ativa}: {e}"); continue

            for index, item in enumerate(tarefas_da_cidade):
                ID_DADOS, CODIGO_BARRAS = item["id_dados"], item["codigo_barras"]
                print(f"\n🔹 [{index + 1}/{len(tarefas_da_cidade)}] Coletando gráficos para ID PAI: {ID_DADOS} | EAN: {CODIGO_BARRAS}")
                
                houve_gravacao_global = False
                concorrentes_db = executar_banco('SELECT ean_concorrente FROM dados_concorrentes WHERE dados_id = %s', d=[ID_DADOS], ler=True)
                lista_eans = [("PAI", CODIGO_BARRAS)] + [("CONCORRENTE", c.get("ean_concorrente") or c.get("EAN_CONCORRENTE")) for c in concorrentes_db if (c.get("ean_concorrente") or c.get("EAN_CONCORRENTE")) and (c.get("ean_concorrente") or c.get("EAN_CONCORRENTE")) != "SEM_CONCORRENTES"]

                try:
                    for tipo_produto, ean_alvo in lista_eans:
                        # 1️⃣ ETAPA 1: Carrega a página estável do EAN da vez primeiro
                        print(f"   ↳ Abrindo listagem para [{tipo_produto}]: {ean_alvo}")
                        nav.execute_script(f"window.current_ean_target = '{ean_alvo}'; window.dados_precos = [];")
                        
                        if tipo_produto == "CONCORRENTE":
                            try: campo = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='fake-sbar'] | //input[@id='sbar']")))
                            except: nav.get("https://precodahora.ba.gov.br"); time.sleep(4); fechar_popup_anuncio(); campo = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='fake-sbar'] | //input[@id='sbar']")))
                            campo.click()
                        
                        fazer_pesquisa_ean(ean_alvo); verificar_e_resolver_captcha(); forcar_rolagem_pagina()
                        
                        # 2️⃣ ETAPA 2: Varre os históricos de gráficos reais sem coletar preços correntes da gôndola
                        xpath_lista_botoes = "//button[contains(@class, 'btn-historico') or contains(@class, 'historico')] | //table//button[contains(translate(., 'historico', 'HISTORICO'), 'HISTÓRICO')] | //a[contains(@class, 'btn-historico')]"
                        botoes_lojas = nav.find_elements(By.XPATH, xpath_lista_botoes)
                        print(f"   📊 Localizados {len(botoes_lojas)} estabelecimentos. Percorrendo modais...")
                        
                        for idx in range(len(botoes_lojas)):
                            try:
                                frescas = nav.find_elements(By.XPATH, xpath_lista_botoes)
                                if idx >= len(frescas): break
                                
                                nav.execute_script("window.dados_precos = [];")
                                abrir_grafico_historico(frescas[idx])
                                
                                lista_api_hist = nav.execute_script("return window.dados_precos;")
                                h_loja_local = decodificar_dados_grafico(lista_api_hist, ean_alvo, ID_DADOS, tipo_produto)
                                
                                if h_loja_local:
                                    salvar_historico_grafico_banco_e_excel(h_loja_local)
                                    houve_gravacao_global = True
                            except Exception: pass
                            
                except Exception as e_prod:
                    print(f"   [ERRO] Falha crítica no EAN {CODIGO_BARRAS}: {e_prod}")

                # 🚀 ATUALIZAÇÃO INDEPENDENTE DE STATUS: Baixa apenas na esteira de históricos
                if houve_gravacao_global:
                    executar_banco("UPDATE DADOS SET status_historico = 'PROCESSADO' WHERE ID = %s", d=[int(ID_DADOS)])
                    print(f"   💾 ID {ID_DADOS} baixado na fila como PROCESSADO.")
                else:
                    executar_banco("UPDATE DADOS SET status_historico = 'PROCESSADO_VAZIO' WHERE ID = %s", d=[int(ID_DADOS)])
                nav.execute_script("window.dados_precos = [];")

        print("\n🏁 [CICLO CONCLUÍDO] Todos os históricos da rodada foram salvos. Reiniciando lote..."); time.sleep(180)
except KeyboardInterrupt: 
    print("\n[AVISO] Parado pelo usuário.")
finally:
    if 'nav' in locals(): nav.quit()
