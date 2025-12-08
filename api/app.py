import re
from bs4 import BeautifulSoup, NavigableString
import streamlit as st
import json
import lxml.html
import lxml.etree
import pandas as pd

# --- 1. CONFIGURACIÓN ---
MIN_HIJOS_REQUERIDOS = 3
UMBRAL_FRECUENCIA = 0.6


# --- 2. FUNCIONES AUXILIARES PARA EL RASTREO (CSS Path y XPath) ---

def get_css_path(tag):
    path = []
    for parent in tag.parents:
        if parent is None or parent.name == '[document]': break
        sibling_index = 0
        tag_name = parent.name
        siblings = list(parent.parent.children) if parent.parent else []
        for sibling in siblings:
            if sibling.name == tag_name: sibling_index += 1
            if sibling is parent:
                path.append(f"{tag_name}:nth-of-type({sibling_index})");
                break
    return " > ".join(reversed(path))


def get_xpath(tag):
    path = []
    for parent in tag.parents:
        if parent is None or parent.name == '[document]': break
        tag_name = parent.name
        count = 1
        for sibling in parent.previous_siblings:
            if sibling.name == tag_name: count += 1
        path.append(f"{tag_name}[{count}]")
    return "/" + "/".join(reversed(path))


# --- 3. FUNCIONES DE LÓGICA DE PATRONES ---

def generar_huella(tag):
    if not tag or not hasattr(tag, 'name') or tag.name is None: return ""
    parts = []
    for child in tag.find_all(True, recursive=False):
        child_huella = generar_huella(child)
        parts.append(child.name + (f"[{child_huella}]" if child_huella else ""))
    return "+".join(parts)


def obtener_atributos_descendientes_con_ruta_y_texto(tag):
    """
    Recorre el tag y sus descendientes, devolviendo una lista de tuplas:
    (ruta_relativa_key, nombre_atributo_o_text, valor)
    """
    atributos_encontrados = []

    for i, descendant in enumerate(tag.descendants):

        is_tag = hasattr(descendant, 'name') and descendant.name is not None

        # A. PROCESAR ATRIBUTOS (Solo si es una etiqueta)
        if is_tag:
            relative_key = f"{descendant.name}[{i}]"
            for attr_name, attr_value in descendant.attrs.items():
                value = " ".join(attr_value) if isinstance(attr_value, list) else str(attr_value)
                atributos_encontrados.append((relative_key, attr_name, value))

        # B. PROCESAR TEXTO (Solo si es una cadena de texto navegable)
        elif isinstance(descendant, NavigableString):
            clean_text = str(descendant).strip()
            if clean_text:
                parent_name = descendant.parent.name if descendant.parent and hasattr(descendant.parent,
                                                                                      'name') else 'unknown'
                text_key = f"{parent_name}_text[{i}]"
                atributos_encontrados.append((text_key, "text_content", clean_text))

    return atributos_encontrados


# --- 4. ALGORITMO PRINCIPAL (Devuelve metadata y datos de instancias) ---

def encontrar_contenedores_relevantes(html_content):
    soup = BeautifulSoup(html_content, 'lxml')
    contenedores_encontrados = []
    instancias_encontradas = []

    # Set para rastrear los XPaths ya procesados (solución a la duplicidad)
    xpaths_unicos = set()

    for elemento_padre in soup.find_all(True):
        hijos = [h for h in elemento_padre.contents if hasattr(h, 'name') and h.name is not None]
        if len(hijos) < MIN_HIJOS_REQUERIDOS: continue

        agrupados_por_huella = {}
        for hijo in hijos:
            huella = generar_huella(hijo)
            if huella:
                if huella not in agrupados_por_huella: agrupados_por_huella[huella] = []
                agrupados_por_huella[huella].append(hijo)
        if not agrupados_por_huella: continue

        huella_dominante, unidades_semanticas = max(
            agrupados_por_huella.items(), key=lambda item: len(item[1])
        )
        frecuencia_maxima = len(unidades_semanticas)

        if (frecuencia_maxima / len(hijos)) >= UMBRAL_FRECUENCIA:

            # 1. Identificar variables
            lista_de_atributos_por_unidad = [
                obtener_atributos_descendientes_con_ruta_y_texto(unidad)
                for unidad in unidades_semanticas
            ]
            mapa_valores_por_clave = {}
            for atributos_unidad in lista_de_atributos_por_unidad:
                for relative_key, attr_name, attr_value in atributos_unidad:
                    clave_atributo = (relative_key, attr_name)
                    if clave_atributo not in mapa_valores_por_clave: mapa_valores_por_clave[clave_atributo] = []
                    mapa_valores_por_clave[clave_atributo].append(attr_value)

            variable_attrs_final = []
            for (relative_key, attr_name), valores in mapa_valores_por_clave.items():
                if len(valores) == frecuencia_maxima:
                    if len(set(valores)) > 1:
                        variable_attrs_final.append(f"{relative_key}@{attr_name}")

            # ====================================================================
            # <<< CAMBIO SOLICITADO: FILTRAR CONTENEDORES SIN VARIABLES SEMÁNTICAS >>>
            # Si no se encontró ninguna variable (es decir, todas las unidades son idénticas),
            # descartamos el contenedor.
            if not variable_attrs_final:
                continue
            # ====================================================================

            # --- EXTRAER DATOS DE INSTANCIA ---
            data_instances = []
            for unidad in unidades_semanticas:

                unidad_lookup_map = {}
                for relative_key, attr_name, attr_value in obtener_atributos_descendientes_con_ruta_y_texto(unidad):
                    unidad_lookup_map[f"{relative_key}@{attr_name}"] = attr_value

                instance_data = {}
                for attr_key in variable_attrs_final:
                    instance_data[attr_key] = unidad_lookup_map.get(attr_key, "N/A (No Encontrado)")

                data_instances.append(instance_data)

            # 3. Construir metadata y objeto de instancia
            if unidades_semanticas:
                unidad_ejemplo = unidades_semanticas[0]
                container_xpath = get_xpath(elemento_padre)

                # *** FILTRO DE UNICIDAD ***
                if container_xpath in xpaths_unicos:
                    continue  # Saltar si este XPath ya ha sido procesado
                xpaths_unicos.add(container_xpath)
                # **************************

                preview = elemento_padre.prettify().splitlines()[0]
                if len(preview) > 80:
                    preview = preview[:77] + "..."
                elif not preview.endswith('>'):
                    preview = preview + "..."

                # Metadata del Contenedor
                resultado = {
                    "container_tag": elemento_padre.name,
                    "unit_root_tag": unidad_ejemplo.name,
                    "unit_count": frecuencia_maxima,
                    "container_css_path": get_css_path(elemento_padre),
                    "container_xpath": container_xpath,
                    "container_preview": preview,
                    "semantic_variable_attrs": variable_attrs_final,
                    "dominant_huella": huella_dominante
                }
                contenedores_encontrados.append(resultado)

                # Datos de las Instancias
                instancia_contenedor = {
                    "container_xpath": container_xpath,
                    "unit_root_tag": unidad_ejemplo.name,
                    "instances": data_instances
                }
                instancias_encontradas.append(instancia_contenedor)

    # Filtrar también las instancias para garantizar que solo devolvemos las asociadas a XPaths únicos.
    instancias_filtradas = []
    xpaths_instancias_unicos = set()
    for inst in instancias_encontradas:
        if inst['container_xpath'] not in xpaths_instancias_unicos:
            instancias_filtradas.append(inst)
            xpaths_instancias_unicos.add(inst['container_xpath'])

    return contenedores_encontrados, instancias_filtradas


# -------------------------------------------------------------
# --- LÓGICA DE STREAMLIT ---
# -------------------------------------------------------------

st.set_page_config(layout="wide", page_title="Analizador de Patrones HTML")

# -------------------------------------------------------------
# *** INYECCIÓN DE CSS PARA CORRECCIÓN DE SCROLL Y ALTURA (V3) ***
# -------------------------------------------------------------
st.markdown("""
<style>
/* 1. Forzar al cuerpo de la app a tomar el 100% de la altura de la ventana y ocultar el scroll global. */
.stApp {
    height: 100vh;
    overflow: hidden !important; 
    padding-bottom: 0 !important;
}

/* 2. ELIMINAR EL MARGEN/PADDING INFERIOR Y SUPERIOR DEL CONTENIDO PRINCIPAL */
.main-content {
    padding-top: 1rem; 
    padding-bottom: 0 !important; 
}
</style>
""", unsafe_allow_html=True)
# -------------------------------------------------------------


# Estado de sesión
if 'selected_container' not in st.session_state:
    st.session_state.selected_container = None
if 'html_content' not in st.session_state:
    st.session_state.html_content = None
if 'all_instances_data' not in st.session_state:
    st.session_state.all_instances_data = []


def select_container(container_data):
    """Callback para establecer el contenedor seleccionado."""
    st.session_state.selected_container = container_data


def clear_session_state():
    """Limpia el estado al cargar nuevo HTML."""
    st.session_state.selected_container = None
    st.session_state.all_instances_data = []


# --- BARRA LATERAL (SIDEBAR) ---

st.sidebar.title("🛠️ Entrada de Datos HTML")
st.sidebar.markdown("Sube o pega el HTML para analizar los patrones.")

# Opción 1: Subir Archivo
uploaded_file = st.sidebar.file_uploader("1. Subir Archivo HTML/TXT", type=['html', 'txt'],
                                         on_change=clear_session_state)

if uploaded_file is not None:
    st.session_state.html_content = uploaded_file.read().decode("utf-8")
    st.sidebar.success(f"Archivo '{uploaded_file.name}' cargado.")

st.sidebar.markdown("---")

# Opción 2: Text Area
html_input = st.sidebar.text_area(
    "2. Pegar/Escribir HTML:",
    height=300,
    placeholder="Pega aquí el código HTML...",
    key="html_textarea"
)

# Botón de Análisis para el Text Area
if st.sidebar.button("⚙️ Analizar HTML Pegado"):
    clear_session_state()
    if html_input.strip():
        st.session_state.html_content = html_input
        st.info("Análisis iniciado con el contenido del Text Area.")
    else:
        st.sidebar.warning("El área de texto está vacía. Por favor, introduce el HTML.")

# -------------------------------------------------------------
# --- EJECUCIÓN DEL ALGORITMO Y VISUALIZACIÓN ---
# -------------------------------------------------------------

contenedores_encontrados = []
instancias_encontradas = []

if st.session_state.html_content:

    html_content = st.session_state.html_content

    try:
        contenedores_encontrados, instancias_encontradas = encontrar_contenedores_relevantes(html_content)
        st.session_state.all_instances_data = instancias_encontradas

    except Exception as e:
        st.error("Ocurrió un error inesperado durante el procesamiento del HTML:")
        st.exception(e)
        contenedores_encontrados = []
        st.session_state.all_instances_data = []

    # --- DISEÑO: 1/3 y 2/3 columnas (Grid Principal) ---
    col_containers, col_instances = st.columns([1, 2])

    # Altura utilizada para los contenedores de scroll interno
    CONTAINER_HEIGHT = 900

    # --- Columna Izquierda: Tarjetas de Contenedores (1/3) ---
    with col_containers:
        st.subheader("Contenedores Encontrados")

        if not contenedores_encontrados:
            st.info("No se encontraron patrones repetitivos.")

        # Contenedor con altura (scroll interno)
        with st.container(height=CONTAINER_HEIGHT):
            for i, res in enumerate(contenedores_encontrados):

                is_selected = st.session_state.selected_container and st.session_state.selected_container[
                    'container_xpath'] == res['container_xpath']

                # --- ESTILO DE TARJETA ---
                base_style = "border: 1px solid #ddd; border-radius: 5px; padding: 1px 0 0 0; margin-bottom: 10px;"
                if is_selected:
                    base_style += " border: 2px solid #1ABC9C; background-color: #1a1a1a;"

                st.markdown(f'<div style="{base_style}">', unsafe_allow_html=True)

                with st.expander(f"Contenedor #{i + 1} | {res['container_tag'].upper()} (x{res['unit_count']})",
                                 expanded=False):

                    st.markdown('<div style="padding: 10px;">', unsafe_allow_html=True)

                    st.markdown(f"**Etiqueta Raíz Unidad:** `{res['unit_root_tag']}`")
                    st.markdown(f"**Vista Previa:** `{res['container_preview']}`")

                    # NOTA: Este es el bloque que causaba problemas de anidamiento en algunas versiones de Streamlit
                    with st.expander("Ver Variables y Rutas"):
                        st.code(f"XPath: {res['container_xpath']}", language='text')
                        st.markdown("**Variables Semánticas Detectadas:**")
                        st.code("\n".join(res['semantic_variable_attrs']), language='text')

                    if st.button("Ver Instancias", key=f"select_{i}", on_click=select_container, args=(res,)):
                        pass

                    st.markdown('</div>', unsafe_allow_html=True)

                st.markdown('</div>', unsafe_allow_html=True)

    # --- Columna Derecha: Tarjetas de Instancias (2/3) ---
    with col_instances:
        st.subheader("Instancias de la Unidad Seleccionada")

        if st.session_state.selected_container:
            selected_res = st.session_state.selected_container
            selected_xpath = selected_res['container_xpath']

            instance_group = next(
                (group for group in st.session_state.all_instances_data if group['container_xpath'] == selected_xpath),
                None
            )

            if instance_group:
                instance_data_list = instance_group['instances']

                # Contenedor con altura (scroll interno)
                with st.container(height=CONTAINER_HEIGHT):
                    INSTANCE_COLS = 2
                    cols = st.columns(INSTANCE_COLS)
                    col_index = 0

                    for j, instance_data in enumerate(instance_data_list):
                        with cols[col_index % INSTANCE_COLS]:

                            st.markdown(f"**Instancia #{j + 1}**")
                            st.markdown("---")

                            for attr_key, value in instance_data.items():
                                display_value = value if len(value) < 100 else value[:97] + "..."
                                st.markdown(f"**{attr_key}**: `{display_value}`")

                            st.markdown("<br>", unsafe_allow_html=True)

                            col_index += 1

            else:
                st.error("Error: No se encontraron datos de instancias para el XPath seleccionado.")

        else:
            pass

    # -------------------------------------------------------------------------
    # --- NUEVA SECCIÓN: SALIDAS JSON (Debajo de los grids principales) ---
    # -------------------------------------------------------------------------

    st.markdown("---")
    st.header("📄 Resultados Crudos para Copiar (JSON)")

    col_json_containers, col_json_instances = st.columns(2)

    # Salida de Contenedores
    with col_json_containers:
        st.subheader("Contenedores Detectados")

        # Convertir la lista de diccionarios a una cadena JSON formateada
        json_contenedores = json.dumps(contenedores_encontrados, indent=2, ensure_ascii=False)

        # Usar st.code para mostrar y permitir la copia
        st.code(json_contenedores, language='json', line_numbers=True)

    # Salida de Instancias
    with col_json_instances:
        st.subheader("Datos de Todas las Instancias")

        # Convertir la lista de diccionarios a una cadena JSON formateada
        json_instancias = json.dumps(instancias_encontradas, indent=2, ensure_ascii=False)

        # Usar st.code para mostrar y permitir la copia
        st.code(json_instancias, language='json', line_numbers=True)

    st.markdown("---")