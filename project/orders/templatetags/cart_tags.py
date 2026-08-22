from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    if not isinstance(dictionary, dict):
        return None
    return dictionary.get(str(key))


@register.filter
def sub(value, arg):
    try:
        return value - arg
    except (TypeError, ValueError):
        return None


@register.filter
def price_for_client(unit, user):
    """سعر الوحدة الفعلي حسب حالة العميل (جملة/قطاعي) — للاستخدام في المتجر."""
    if unit is None:
        return None
    client = user if getattr(user, 'is_authenticated', False) else None
    return unit.get_price_for_client(client)


@register.filter
def discount_percent_for_client(unit, user):
    """
    نسبة الخصم (لو موجودة) لهذه الوحدة حسب نوع حساب العميل — 0 لو مفيش خصم
    مسجّل لهذا الصنف أو العميل مش مسجّل دخول. المصدر get_pricing_breakdown_for_client
    (نفس المصدر الوحيد للتسعير المستخدم في السلة/الطلب/الفاتورة) عشان النسبة
    المعروضة كبادچ في كارت المتجر (المرحلة 5) تفضل متطابقة مع السعر الفعلي
    اللي هيتحسب وقت الشراء، بدل ما تتحسب بشكل منفصل وتختلف بالغلط.
    """
    if unit is None:
        return 0
    client = user if getattr(user, 'is_authenticated', False) else None
    if client is None:
        return 0
    _, discount_percent, _ = unit.get_pricing_breakdown_for_client(client)
    return discount_percent


def _client_of(user):
    return user if getattr(user, 'is_authenticated', False) else None


@register.filter
def units_for_client(product, user):
    """الوحدة (أو الوحدات) المفروض تظهر لهذا العميل — انظر Product.units_for_client."""
    if product is None:
        return []
    return product.units_for_client(_client_of(user))

