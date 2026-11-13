"""
Лексиконний матчер: сирий текст допису → рішення про релевантність.

Це дешевий префільтр перед мовною моделлю, а не заміна їй. Його задача —
відсіяти очевидне сміття з потоку в десятки тисяч дописів на добу, щоб
анотатор працював лише з кандидатами.
"""
import hashlib
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from app.demand.products import ProductSpec, ACTIVE_PRODUCTS

_FLAGS = re.IGNORECASE | re.UNICODE


def _compile(patterns: Sequence[str]) -> List[re.Pattern]:
    return [re.compile(p, _FLAGS) for p in patterns]


@dataclass
class MatchResult:
    product_code: str
    matched_terms: List[str]
    sku_codes: List[str]
    looks_promo: bool


class LexiconMatcher:
    def __init__(self, products: Sequence[ProductSpec] = ACTIVE_PRODUCTS):
        self.products = tuple(products)
        self._include: Dict[str, List[re.Pattern]] = {}
        self._exclude: Dict[str, List[re.Pattern]] = {}
        self._promo: Dict[str, List[re.Pattern]] = {}
        self._ambiguous: Dict[str, List[Tuple[re.Pattern, List[re.Pattern]]]] = {}
        self._sku: Dict[str, List[Tuple[str, List[re.Pattern]]]] = {}
        for spec in self.products:
            self._include[spec.code] = _compile(spec.include)
            self._exclude[spec.code] = _compile(spec.exclude)
            self._promo[spec.code] = _compile(spec.promo_hints)
            self._ambiguous[spec.code] = [
                (re.compile(term, _FLAGS), _compile(ctx))
                for term, ctx in spec.ambiguous.items()
            ]
            self._sku[spec.code] = [
                (sku_code, _compile(pats)) for sku_code, pats in spec.sku.items()
            ]

    def match(self, text: str) -> List[MatchResult]:
        if not text:
            return []
        results: List[MatchResult] = []
        for spec in self.products:
            code = spec.code
            if any(p.search(text) for p in self._exclude[code]):
                continue

            hits = [p.pattern for p in self._include[code] if p.search(text)]

            # Неоднозначний термін зараховується лише за наявності контексту.
            for term_re, ctx_res in self._ambiguous[code]:
                if term_re.search(text) and any(c.search(text) for c in ctx_res):
                    hits.append(term_re.pattern)

            if not hits:
                continue

            skus = [s for s, pats in self._sku[code] if any(p.search(text) for p in pats)]
            promo = any(p.search(text) for p in self._promo[code])
            results.append(MatchResult(code, hits, skus, promo))
        return results


def normalize_for_hash(text: str) -> str:
    """Нормалізація для виявлення точних передруків і копіпасти."""
    t = text.lower()
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize_for_hash(text).encode("utf-8")).hexdigest()[:32]
