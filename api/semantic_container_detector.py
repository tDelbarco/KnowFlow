#!/usr/bin/env python3
"""
semantic_container_detector.py

Detector de contenedores semánticos y atributos (versión 100% estadística).
No usa listas hardcodeadas de atributos ni regex "temáticos".
"""

from bs4 import BeautifulSoup
from collections import defaultdict
import statistics
from typing import List, Dict, Any

# -------------------------
# Extracción simple de atributos (sin heurísticas externas)
# -------------------------
def collect_attrs(unit) -> Dict[str, str]:
    """
    Recolecta atributos en la raíz y descendientes con paths relativos,
    además del 'text' virtual.
    """
    attrs = {}

    # root attrs
    for k, v in unit.attrs.items():
        attrs[f"self::{k}"] = ' '.join(v) if isinstance(v, list) else str(v)

    # texto interno
    text = unit.get_text(" ", strip=True)
    if text:
        attrs["self::text"] = text

    # descendientes
    for child in unit.find_all(recursive=True):
        if not getattr(child, "name", None):
            continue

        # construir path relativo
        path_parts = []
        node = child
        while node is not None and node != unit:
            if getattr(node, "name", None):
                path_parts.append(node.name)
            node = node.parent

        rel_path = "/".join(reversed(path_parts)) if path_parts else child.name

        # atributos del descendiente
        for k, v in child.attrs.items():
            attrs[f"{rel_path}::{k}"] = ' '.join(v) if isinstance(v, list) else str(v)

        # texto del descendiente
        txt = child.get_text(" ", strip=True)
        if txt:
            attrs[f"{rel_path}::text"] = txt

    return attrs


# -------------------------
# Análisis puramente estadístico por contenedor
# -------------------------
def analyze_container(container, min_units: int = 2, threshold_shared: float = 0.95, threshold_variable_presence: float = 0.3):
    """
    Analiza un contenedor (solo sus hijos inmediatos) y clasifica atributos en:
      - shared_attrs
      - semantic_variable_attrs
      - noise_attrs
    """
    units = [c for c in container.find_all(recursive=False) if getattr(c, "name", None)]
    if len(units) < min_units:
        return None

    attrs_per_unit = [collect_attrs(u) for u in units]
    all_attr_keys = set().union(*[set(d.keys()) for d in attrs_per_unit])

    n = len(units)
    stats = {}

    for attr in all_attr_keys:
        values = [d.get(attr) for d in attrs_per_unit]
        present = sum(1 for v in values if v is not None)
        existing_values = [v for v in values if v is not None]

        if existing_values:
            lengths = [len(v) for v in existing_values]
            length_var = statistics.pvariance(lengths) if len(lengths) > 1 else 0.0
            uniq = len(set(existing_values))
            avg_length = statistics.mean(lengths)
        else:
            length_var = 0.0
            uniq = 0
            avg_length = 0.0

        stats[attr] = {
            "present": present,
            "present_ratio": present / n,
            "unique_values": uniq,
            "unique_ratio": (uniq / present) if present else 0.0,
            "avg_length": avg_length,
            "length_var": length_var,
            "sample_values": existing_values[:6],
        }

    shared = []
    variable = []
    noise = []

    # Clasificación puramente estadística
    for attr, s in stats.items():
        p = s["present_ratio"]
        u = s["unique_ratio"]
        complexity = s["avg_length"] + s["length_var"]

        if p >= threshold_shared:
            if u < 0.15 and complexity < 8:
                shared.append(attr)
            else:
                variable.append(attr)
            continue

        if p >= threshold_variable_presence:
            if u > 0.15 or complexity > 10:
                variable.append(attr)
            else:
                noise.append(attr)
            continue

        noise.append(attr)

    shared.sort()
    variable.sort()
    noise.sort()

    return {
        "container_tag": container.name,
        "unit_root_tag": units[0].name if units else None,
        "unit_count": n,
        "shared_attrs": shared,
        "semantic_variable_attrs": variable,
        "noise_attrs": noise,
        "attr_stats": stats,
    }


# -------------------------
# Verificación de que un elemento es contenedor válido
# -------------------------
def es_contenedor_valido(elem, min_units: int = 2, max_child_tag_variation: int = 3) -> bool:
    hijos = [c for c in elem.find_all(recursive=False) if getattr(c, "name", None)]

    if len(hijos) < min_units:
        return False

    nombres_hijos = [c.name for c in hijos]

    if len(set(nombres_hijos)) > max_child_tag_variation and len(hijos) < 6:
        return False

    return True


# -------------------------
# Detección de candidatos contenedores
# -------------------------
def candidate_containers(soup, container_tags: List[str] = None, min_units: int = 2):
    if container_tags is None:
        container_tags = ["div", "span", "section", "article", "ul", "ol"]

    raw = [c for c in soup.find_all(container_tags)]
    valids = [c for c in raw if es_contenedor_valido(c, min_units=min_units)]

    final = []
    for c in valids:
        parent = c.parent
        is_nested = False
        while parent is not None and getattr(parent, "name", None):
            if parent in valids:
                is_nested = True
                break
            parent = parent.parent

        if not is_nested:
            final.append(c)

    return final


# -------------------------
# Análisis global del HTML
# -------------------------
def analyze_html_all(html: str, min_units: int = 2):
    soup = BeautifulSoup(html, "html.parser")
    containers = candidate_containers(soup, min_units=min_units)

    results = []
    for c in containers:
        r = analyze_container(c, min_units=min_units)
        if r:
            results.append(r)

    results.sort(
        key=lambda x: (
            x["unit_count"],
            len(x["semantic_variable_attrs"]),
            len(x["shared_attrs"]),
        ),
        reverse=True,
    )

    return results
