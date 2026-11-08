from rapidfuzz import fuzz, process

from app.schemas import OrderChange, SaleLine, StockLine

MATCH_THRESHOLD = 80

# Palabras que el tendero agrega y que no ayudan a encontrar el producto ("unas cocas de litro").
FILLER_WORDS = {"de", "del", "la", "las", "el", "los", "un", "una", "unos", "unas"}


def catalog_by_sku(products: list[dict]) -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    for product in products:
        sku = str(product.get("sku") or "").strip()
        if sku:
            mapping[sku.casefold()] = product
    return mapping


def product_terms(product: dict) -> list[str]:
    terms: list[str] = []
    sku = str(product.get("sku") or "").strip()
    name = str(product.get("name") or "").strip()
    if sku:
        terms.append(sku)
    if name:
        terms.append(name)
    for alias in str(product.get("aliases") or "").split(","):
        cleaned = alias.strip()
        if cleaned:
            terms.append(cleaned)
    return terms


def _singular(word: str) -> str:
    """Plural sencillo: "cocas" -> "coca", "leches" -> "leche". Las palabras cortas no se tocan."""
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def query_variants(query: str) -> list[str]:
    """Lo que dijo el tendero y, si cambia, la misma frase sin plurales ni palabras de relleno."""
    words = [_singular(word) for word in query.casefold().split() if word not in FILLER_WORDS]
    normalized = " ".join(words)
    return [query] if not normalized or normalized == query.casefold() else [query, normalized]


def product_score(variants: list[str], terms: list[str], cutoff: float) -> float | None:
    """El mejor parecido entre las formas de la frase y los nombres del producto (None si no llega al mínimo)."""
    best: float | None = None
    for variant in variants:
        result = process.extractOne(variant, terms, scorer=fuzz.WRatio, score_cutoff=cutoff)
        if result is not None and (best is None or result[1] > best):
            best = result[1]
    return best


def best_product_match(query: str, products: list[dict]) -> dict | None:
    query = (query or "").strip()
    if not query or not products:
        return None

    variants = query_variants(query)
    best: dict | None = None
    best_score = -1.0
    tied = False
    for product in products:
        terms = product_terms(product)
        if not terms:
            continue
        score = product_score(variants, terms, MATCH_THRESHOLD)
        if score is None:
            continue
        product_sku = str(product.get("sku") or "")
        best_sku = str(best.get("sku") or "") if best is not None else ""
        if score > best_score:
            best = product
            best_score = score
            tied = False
        elif score == best_score and product_sku != best_sku:
            tied = True
    if tied:
        return None
    return best


SUGGESTION_THRESHOLD = 60


def search_products(query: str, products: list[dict], limit: int = 3) -> list[dict]:
    """Los productos más parecidos al texto, del más al menos parecido (para sugerir opciones)."""
    query = (query or "").strip()
    if not query:
        return []
    variants = query_variants(query)
    scored: list[tuple[float, dict]] = []
    for product in products:
        terms = product_terms(product)
        if not terms:
            continue
        score = product_score(variants, terms, SUGGESTION_THRESHOLD)
        if score is not None:
            scored.append((score, product))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [product for _score, product in scored[:limit]]


def exact_term_match(query_text: str, products: list[dict]) -> dict | None:
    """El producto cuyo nombre o alias es exactamente lo que escribió el tendero (sin mayúsculas ni plural)."""
    wanted = {variant.casefold().strip() for variant in query_variants((query_text or "").strip())} - {""}
    if not wanted:
        return None
    found = [
        product for product in products
        if any(term.casefold().strip() in wanted for term in product_terms(product))
    ]
    return found[0] if len(found) == 1 else None


def resolve_sku(sku: str | None, query_text: str, products: list[dict]) -> str | None:
    by_sku = catalog_by_sku(products)
    cleaned_sku = (sku or "").strip()
    # Si lo que dijo el tendero es justo el nombre o un alias de un producto, manda eso sobre el sku que
    # propone el modelo ("aceite litro" es Aceite Gourmet 1 litro aunque Grok mande el de 3 litros).
    exact = exact_term_match(query_text, products)
    if exact is not None:
        return str(exact["sku"])
    if cleaned_sku:
        found = by_sku.get(cleaned_sku.casefold())
        if found is not None:
            return str(found["sku"])

    query = (query_text or "").strip() or cleaned_sku
    match = best_product_match(query, products)
    if match is None:
        return None
    return str(match["sku"])


def match_sale_lines(lines: list[SaleLine], products: list[dict]) -> list[SaleLine]:
    matched: list[SaleLine] = []
    for line in lines:
        sku = resolve_sku(line.sku, line.product_text, products)
        matched.append(line.model_copy(update={"sku": sku}))
    return matched


def match_stock_lines(lines: list[StockLine], products: list[dict]) -> list[StockLine]:
    matched: list[StockLine] = []
    for line in lines:
        sku = resolve_sku(line.sku, "", products)
        matched.append(line.model_copy(update={"sku": sku}))
    return matched


def match_order_changes(changes: list[OrderChange], products: list[dict]) -> list[OrderChange]:
    return [
        change.model_copy(update={"sku": resolve_sku(change.sku, change.product_text, products)})
        for change in changes
    ]
