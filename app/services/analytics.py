from dataclasses import asdict, dataclass


@dataclass
class KPI:
    name: str
    value: float
    unit: str
    status: str
    note: str


def classify(value: float, warning: float, critical: float, inverse: bool = False) -> str:
    if inverse:
        return "red" if value >= critical else "yellow" if value >= warning else "green"
    return "green" if value >= warning else "yellow" if value >= critical else "red"


def snapshot() -> dict:
    """Returns a stable empty-state payload until first Ozon sync completes."""
    rows = [
        KPI("Выручка", 0, "₽", "neutral", "Появится после первой синхронизации"),
        KPI("Заказы", 0, "шт.", "neutral", "Появится после первой синхронизации"),
        KPI("CTR рекламы", 0, "%", "neutral", "Появится после первой синхронизации"),
        KPI("Остатки с риском", 0, "SKU", "neutral", "Появится после первой синхронизации"),
    ]
    return {"status": "waiting_for_sync", "kpis": [asdict(x) for x in rows], "insights": []}
