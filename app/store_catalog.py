"""Catálogo de una tienda: los productos de todos sus proveedores aceptados, juntos y comparados.

- load_items: artículos de la tienda (app/catalog_merge.py), cada uno con su proveedor preferido.
- store_products: un producto por artículo (el del proveedor preferido). Es lo que usan las ventas,
  la foto y el agente: así una venta de Coca-Cola queda con el producto del proveedor preferido.
- choose: el tendero escoge a qué proveedor pedirle un artículo.
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog_merge import Item, Offer, find_item, group_offers
from app.models import SUPPLIER_APPROVED, Distributor, Product, Store, StoreProductChoice, StoreSupplier


async def supplier_ids(session: AsyncSession, store: Store) -> list[int]:
    """Proveedores aceptados de la tienda (activos). Una tienda de antes sin la tabla: su proveedor."""
    ids = list(
        (
            await session.scalars(
                select(StoreSupplier.distributor_id)
                .join(Distributor, Distributor.id == StoreSupplier.distributor_id)
                .where(
                    StoreSupplier.store_id == store.id,
                    StoreSupplier.status == SUPPLIER_APPROVED,
                    Distributor.status == "active",
                )
                .order_by(StoreSupplier.decided_at, StoreSupplier.id)
            )
        ).all()
    )
    if not ids and store.distributor_id is not None:
        ids = [store.distributor_id]
    return ids


async def load_items(session: AsyncSession, store: Store) -> list[Item]:
    ids = await supplier_ids(session, store)
    if not ids:
        return []
    rows = (
        await session.execute(
            select(Product, Distributor.name)
            .join(Distributor, Distributor.id == Product.distributor_id)
            .where(Product.distributor_id.in_(ids), Product.active.is_(True))
        )
    ).all()
    chosen = set(
        (await session.scalars(select(StoreProductChoice.product_id).where(StoreProductChoice.store_id == store.id))).all()
    )
    return group_offers([Offer(product, product.distributor_id, name) for product, name in rows], chosen)


def item_products(items: list[Item]) -> list[Product]:
    """Un producto por artículo (el del proveedor preferido), que también se encuentra por los nombres y
    alias de los otros proveedores de ese artículo.

    Sin esto, si el preferido es "Gaseosa Postobón 1.5 L", el tendero que dice "Postobón 1.5 litros"
    (como lo llama otro proveedor) no encuentra el producto. Los alias extra van en un atributo que no
    se guarda en la base (search_aliases).
    """
    products = []
    for item in items:
        product = item.preferred.product
        terms = [product.aliases or ""]
        for offer in item.offers:
            if offer.product is not product:
                terms += [offer.product.name or "", offer.product.aliases or ""]
        product.search_aliases = ",".join(term for term in terms if term)
        products.append(product)
    return products


async def store_products(session: AsyncSession, store: Store) -> list[Product]:
    """Un producto por artículo: el del proveedor preferido."""
    return item_products(await load_items(session, store))


async def choose(session: AsyncSession, store: Store, items: list[Item], product_id: int) -> Item:
    """El tendero escoge el producto (y con él el proveedor) de un artículo."""
    item = find_item(items, product_id)
    if item is None:
        raise ValueError("ese producto no es de ninguno de tus proveedores")
    others = [offer.product.id for offer in item.offers]
    await session.execute(
        delete(StoreProductChoice).where(StoreProductChoice.store_id == store.id, StoreProductChoice.product_id.in_(others))
    )
    session.add(StoreProductChoice(store_id=store.id, product_id=product_id))
    item.chosen = next(offer for offer in item.offers if offer.product.id == product_id)
    return item
