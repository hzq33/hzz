"""NameResolver — four-stage alias resolution with full-name supplementation.

Extracted from the former monolithic ``name_resolver.py``; logic unchanged.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict

from src.domain.name_resolver.helpers import (
    _NON_NAME_WORDS,
    _is_structural_match,
    _is_substring_or_similar,
    _strip_honorific,
    _to_simplified,
)
from src.domain.novel.models import CharacterIdentity, Gender, Mention

logger = logging.getLogger("agent")


class NameResolver:
    """Four-stage alias resolver with full-name supplementation.

    Algorithm:
        1. Normalize: TC→SC conversion, whitespace trim
        2. Strip: Remove honorific suffixes
        3. Cluster: Group similar names by substring/edit-distance
        4. Validate: Split clusters where members never co-occur
        5. Supplement: Select/construct canonical full name

    The canonical name is always the longest valid name in the cluster.
    If no name is ≥2 chars, the longest one is still used.
    """

    def resolve(
        self,
        raw_names: list[str],
        co_occurrence: dict[str, Counter] | None = None,
        mentions_data: dict[str, list[Mention]] | None = None,
    ) -> dict[str, CharacterIdentity]:
        """Resolve raw character names into unified CharacterIdentity objects.

        Args:
            raw_names: List of raw character name strings (may contain duplicates/aliases).
            co_occurrence: Co-occurrence counts between names. Used for validation.
                          e.g., {"林晚晴": Counter({"顾清寒": 6, "晚晴": 3})}
            mentions_data: Optional mention records per name, for frequency tracking.

        Returns:
            Dict mapping canonical_name → CharacterIdentity.
            Each identity contains all resolved aliases.
        """
        if co_occurrence is None:
            co_occurrence = {}
        if mentions_data is None:
            mentions_data = {}

        # Deduplicate raw names
        unique_names = list(dict.fromkeys(raw_names))

        # Filter out non-name words
        unique_names = [n for n in unique_names if n and n not in _NON_NAME_WORDS and len(n) >= 1]

        if not unique_names:
            return {}

        # Stage 1: Normalize (TC→SC + trim)
        normalized = []
        for name in unique_names:
            n = _to_simplified(name.strip())
            normalized.append(n)
        unique_names = list(dict.fromkeys(normalized))  # Re-dedup after normalization

        # Stage 2: Strip honorifics — map each name to its stripped form
        name_to_stripped: dict[str, str] = {}
        for name in unique_names:
            stripped = _strip_honorific(name)
            name_to_stripped[name] = stripped

        # Stage 3: Cluster by similarity
        clusters = self._cluster_names(list(name_to_stripped.keys()), name_to_stripped)

        # Stage 4: Validate by co-occurrence — split clusters if needed
        validated_clusters = self._validate_clusters(clusters, co_occurrence)

        # Stage 5: Build CharacterIdentity with full-name supplementation
        result: dict[str, CharacterIdentity] = {}
        for cluster_names in validated_clusters:
            # Collect all names (original + stripped forms)
            all_names: set[str] = set()
            for n in cluster_names:
                all_names.add(n)
                all_names.add(name_to_stripped.get(n, n))
            all_names.discard("")  # Remove empty strings

            # Determine canonical name (longest name)
            canonical = self._select_canonical_name(all_names)

            # Supplement full name if all names are short
            if len(canonical) < 3 and len(all_names) > 1:
                canonical = self._supplement_full_name(all_names, canonical, co_occurrence, cluster_names)

            # Collect mentions
            all_mentions: list[Mention] = []
            total_mentions = 0
            for name in cluster_names:
                ms = mentions_data.get(name, [])
                all_mentions.extend(ms)
                total_mentions += len(ms)
            if not all_mentions:
                # If no explicit mentions data, estimate from raw name count
                total_mentions = len(cluster_names)

            # Infer gender from honorific patterns
            gender = self._infer_gender(all_names)

            identity = CharacterIdentity(
                canonical_name=canonical,
                aliases=frozenset(all_names - {canonical}),
                mentions=all_mentions[:100],  # Cap to prevent memory bloat
                gender=gender,
                total_mentions=total_mentions,
            )

            result[canonical] = identity

        logger.info(
            "NameResolver: %d raw names → %d unique identities: %s",
            len(raw_names),
            len(result),
            list(result.keys()),
        )
        return result

    # ── Stage 3: Clustering ─────────────────────────────

    def _cluster_names(
        self,
        names: list[str],
        name_to_stripped: dict[str, str],
    ) -> list[list[str]]:
        """Cluster names by similarity (substring or edit distance ≤1).

        Uses union-find to merge overlapping pairs.
        """
        if not names:
            return []

        # Union-Find
        parent: dict[str, str] = {n: n for n in names}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: str, y: str) -> None:
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[rx] = ry

        # Compare all pairs
        for i, n1 in enumerate(names):
            s1 = name_to_stripped.get(n1, n1)
            for n2 in names[i + 1:]:
                s2 = name_to_stripped.get(n2, n2)
                if _is_substring_or_similar(s1, s2):
                    union(n1, n2)

        # Collect clusters
        cluster_map: dict[str, list[str]] = defaultdict(list)
        for n in names:
            cluster_map[find(n)].append(n)

        return list(cluster_map.values())

    # ── Stage 4: Co-occurrence validation ───────────────

    def _validate_clusters(
        self,
        clusters: list[list[str]],
        co_occurrence: dict[str, Counter],
    ) -> list[list[str]]:
        """Validate clusters using co-occurrence data.

        If two names in a cluster never co-occur with each other AND
        have different co-occurrence partners, they might be different
        characters → split the cluster.

        CRITICAL: Names that are structurally similar (substring/prefix
        relationship, e.g., "林晚晴" and "晚晴") are NOT split even if
        they "co-occur" — because they're the same person referred to
        differently, and their co-occurrence is an artifact of the text
        mentioning both forms.
        """
        validated: list[list[str]] = []

        for cluster in clusters:
            if len(cluster) <= 1:
                validated.append(cluster)
                continue

            # Check pairwise co-occurrence within the cluster
            should_split = False
            for i, n1 in enumerate(cluster):
                for n2 in cluster[i + 1:]:
                    # Skip validation for structurally similar pairs
                    # (substring/prefix = same person, different forms)
                    s1 = _strip_honorific(n1)
                    s2 = _strip_honorific(n2)
                    if _is_structural_match(s1, s2):
                        continue  # Don't split structural matches

                    # Direct co-occurrence?
                    n1_co = co_occurrence.get(n1, Counter())
                    n2_co = co_occurrence.get(n2, Counter())

                    # If n1 and n2 directly co-occur, they're definitely different people
                    if n2 in n1_co or n1 in n2_co:
                        should_split = True
                        break

                    # Check if they share co-occurrence partners
                    n1_partners = set(n1_co.keys()) - {n1, n2}
                    n2_partners = set(n2_co.keys()) - {n1, n2}
                    shared_partners = n1_partners & n2_partners

                    if shared_partners:
                        # Same social circle → likely same person
                        pass
                    elif n1_partners and n2_partners and not shared_partners:
                        # Different social circles with no overlap → might be different
                        if len(n1_partners) >= 2 and len(n2_partners) >= 2:
                            should_split = True
                            break

                if should_split:
                    break

            if should_split:
                sub_clusters = self._split_conservative(cluster, co_occurrence)
                validated.extend(sub_clusters)
            else:
                validated.append(cluster)

        return validated

    def _split_conservative(
        self,
        cluster: list[str],
        co_occurrence: dict[str, Counter],
    ) -> list[list[str]]:
        """Conservative split: only separate names that co-occur directly.

        Names that are substrings of each other are kept together
        even if they don't co-occur (they're likely the same person
        referred to differently).
        """
        # Group by direct co-occurrence
        groups: list[list[str]] = []
        assigned: set[str] = set()

        for name in cluster:
            if name in assigned:
                continue
            # Start new group with this name
            group = [name]
            assigned.add(name)

            # Add names that are substrings/similar but don't directly co-occur
            for other in cluster:
                if other in assigned:
                    continue
                n_co = co_occurrence.get(name, Counter())
                o_co = co_occurrence.get(other, Counter())

                # If they don't directly co-occur, check if they're substring-related
                if other not in n_co and name not in o_co:
                    if name in other or other in name:
                        group.append(other)
                        assigned.add(other)

            groups.append(group)

        # Add any unassigned names as singletons
        for name in cluster:
            if name not in assigned:
                groups.append([name])

        return groups

    # ── Stage 5: Full-name supplementation ──────────────

    def _select_canonical_name(self, all_names: set[str]) -> str:
        """Select the canonical name — the best full name.

        Priority:
        1. Longest name that is NOT an honorific-bearing form
           (prefer "林晚晴" over "林姐姐" or "晚晴姐")
        2. Longest name overall
        3. Looks like a full name (2-4 chars)
        4. Alphabetical (for determinism)
        """
        if not all_names:
            return ""

        # Split into "clean" (no honorific) and "honorific" names
        clean_names = []
        honorific_names = []
        for name in all_names:
            stripped = _strip_honorific(name)
            if stripped == name:
                clean_names.append(name)
            else:
                honorific_names.append(name)

        # Prefer clean names; fall back to honorific names
        candidate_pool = clean_names if clean_names else honorific_names

        # Sort by: length (desc), looks_like_full_name, alphabetical
        sorted_names = sorted(
            candidate_pool,
            key=lambda n: (
                -len(n),
                not self._looks_like_full_name(n),
                n,
            ),
        )
        return sorted_names[0] if sorted_names else (sorted(all_names)[0] if all_names else "")

    def _looks_like_full_name(self, name: str) -> bool:
        """Heuristic: does this name look like a complete full name?

        Chinese full names are typically 2-4 characters.
        Single chars are usually surnames or given-name-only.
        """
        return 2 <= len(name) <= 4

    def _supplement_full_name(
        self,
        all_names: set[str],
        current_canonical: str,
        co_occurrence: dict[str, Counter],
        cluster_names: list[str],
    ) -> str:
        """Attempt to construct a full name from short aliases.

        Strategy:
        1. If we have a 1-char name (likely surname) and a 2-char name,
           try concatenating: "林" + "晚晴" → "林晚晴"
        2. If we have multiple 1-char names, don't guess
        3. If concatenation results in a name that appears in the text
           (checked via co_occurrence keys), use it

        Returns the supplemented full name, or current_canonical if no improvement.
        """
        # Separate 1-char (likely surname) and 2+char names
        single_chars = [n for n in all_names if len(n) == 1]
        multi_chars = [n for n in all_names if len(n) >= 2]

        # If we already have a 3+char name, no need to supplement
        if any(len(n) >= 3 for n in all_names):
            return current_canonical

        # Try surname + given_name concatenation
        if len(single_chars) == 1 and multi_chars:
            surname = single_chars[0]
            # Try concatenating with each multi-char name
            for given in multi_chars:
                # Skip if given already starts with the surname (avoid "林"+"林晚"="林林晚")
                if given.startswith(surname):
                    # Already has surname — use as-is if it's the longest
                    if len(given) > len(current_canonical):
                        return given
                    continue
                candidate = surname + given
                # Check if this candidate appears in co_occurrence (i.e., was in the text)
                if candidate in co_occurrence:
                    return candidate
                # Even if not in co_occurrence, the concatenation is a reasonable guess
                # Only use it if it's longer than current canonical
                if len(candidate) > len(current_canonical):
                    return candidate

        # Try combining two 2-char names that might be surname+given
        # e.g., "林晚" + "晚晴" → "林晚晴" (overlap merge)
        if len(multi_chars) >= 2:
            for n1 in multi_chars:
                for n2 in multi_chars:
                    if n1 == n2:
                        continue
                    # Check if n1's end overlaps with n2's start
                    for overlap_len in range(min(len(n1), len(n2)), 0, -1):
                        if n1[-overlap_len:] == n2[:overlap_len]:
                            merged = n1 + n2[overlap_len:]
                            if len(merged) >= 3 and len(merged) <= 4:
                                if merged in co_occurrence:
                                    return merged

        return current_canonical

    # ── Gender inference ────────────────────────────────

    def _infer_gender(self, all_names: set[str]) -> Gender | None:
        """Infer gender from name patterns and honorifics.

        Rules:
        - 姐/小姐/夫人/太太/姑娘 → FEMALE
        - 哥/先生/少爷/公子/大爷 → MALE
        - Common female name characters (晴/月/花/雪/梅/兰/婷/娜/妍/蝶/婉/柔)
        - Common male name characters (寒/风/雷/震/天/龙/虎/峰/磊/刚/强)
        - Default: UNKNOWN
        """
        # Check honorifics first (most reliable)
        for name in all_names:
            if any(h in name for h in ["小姐", "夫人", "太太", "姑娘", "姐姐", "妹妹", "师姐", "学妹"]):
                return Gender.FEMALE
            if any(h in name for h in ["少爷", "公子", "先生", "师兄", "学弟", "殿下", "大人", "哥哥", "弟弟"]):
                return Gender.MALE

        # Check name characters (less reliable, only if no honorific clue)
        female_chars = {"晴", "月", "花", "雪", "梅", "兰", "婷", "娜", "妍", "蝶", "婉", "柔", "梦", "瑶", "琳", "颖", "晚"}
        male_chars = {"寒", "风", "雷", "震", "天", "龙", "虎", "峰", "磊", "刚", "强", "墨", "尘", "剑", "武", "豪", "杰", "勇"}

        female_score = 0
        male_score = 0
        for name in all_names:
            for char in name:
                if char in female_chars:
                    female_score += 1
                if char in male_chars:
                    male_score += 1

        if female_score > male_score:
            return Gender.FEMALE
        if male_score > female_score:
            return Gender.MALE

        return Gender.UNKNOWN

    # ── Utility: create name lookup map ─────────────────

    def create_lookup_map(
        self,
        identities: dict[str, CharacterIdentity],
    ) -> dict[str, str]:
        """Create a lookup map from any name/alias → canonical_name.

        Useful for replacing all name references in existing data:
            lookup = resolver.create_lookup_map(identities)
            canonical = lookup.get(raw_name, raw_name)

        Args:
            identities: Output of resolve()

        Returns:
            Dict mapping every name and alias to its canonical_name.
        """
        lookup: dict[str, str] = {}
        for canonical, identity in identities.items():
            lookup[canonical] = canonical
            for alias in identity.aliases:
                lookup[alias] = canonical
                # Also add stripped form
                stripped = _strip_honorific(alias)
                if stripped != alias:
                    lookup[stripped] = canonical
        return lookup
