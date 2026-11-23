"""Semantic equivalence certification for structured rule cards.

Cards describe restrictions over a finite domain: edition-authorized markets,
the edition's dosage form and non-negative integer ages. A card's scope is a
Boolean expression over market / form / age predicates combined with explicit
AND, OR, NOT and named exception references.

The engine compiles both sides (source leaflet and one translated language),
checks structural integrity (unknown enum values, undefined or cyclic
exceptions, duplicate exception definitions, opposite actions overlapping on
the same side) and then compares the *action functions* over the whole
domain -- never sampled points. Age is unbounded above, so every age bound is
used to cut the age axis into elementary integer intervals including the open
tail ``[lo, null]``.

Only material-expression consistency is adjudicated here; nothing clinical.
"""
import hashlib
import json

ATOM_KEYS = ('market', 'form', 'age_years')
COMPOSITE_KEYS = ('all', 'any', 'not', 'except')


def _is_nonneg_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_scope(scope):
    """Return a list of structural problems found in a scope expression."""
    problems = []

    def walk(node, path):
        if not isinstance(node, dict) or not node:
            problems.append({'path': path, 'reason': 'scope_not_a_nonempty_object'})
            return
        keys = set(node)
        composites = keys & set(COMPOSITE_KEYS)
        atoms = keys & set(ATOM_KEYS)
        if composites:
            if len(composites) > 1 or atoms:
                problems.append({'path': path, 'reason': 'mixed_or_multiple_composites'})
                return
            kind = next(iter(composites))
            if kind == 'all' or kind == 'any':
                items = node[kind]
                if not isinstance(items, list) or not items:
                    problems.append({'path': path + '.' + kind, 'reason': 'composite_requires_nonempty_list'})
                    return
                for i, item in enumerate(items):
                    walk(item, path + '.' + kind + '[' + str(i) + ']')
            elif kind == 'not':
                walk(node['not'], path + '.not')
            else:
                if not isinstance(node['except'], str) or not node['except']:
                    problems.append({'path': path + '.except', 'reason': 'except_requires_exception_name'})
            return
        if not atoms:
            problems.append({'path': path, 'reason': 'unknown_scope_key:' + ','.join(sorted(keys))})
            return
        if 'age_years' in node:
            band = node['age_years']
            if (not isinstance(band, list) or len(band) != 2
                    or not _is_nonneg_int(band[0])
                    or not (band[1] is None or (_is_nonneg_int(band[1]) and band[1] >= band[0]))):
                problems.append({'path': path + '.age_years', 'reason': 'invalid_age_band'})
        for key in ('market', 'form'):
            if key in node and (not isinstance(node[key], str) or not node[key]):
                problems.append({'path': path + '.' + key, 'reason': key + '_must_be_string'})

    walk(scope, '$')
    return problems


def _age_bounds(scope):
    """Collect all inclusive lower bounds appearing in a scope expression."""
    found = []

    def walk(node):
        if not isinstance(node, dict) or not node:
            return
        if 'all' in node:
            for item in node['all']:
                walk(item)
        elif 'any' in node:
            for item in node['any']:
                walk(item)
        elif 'not' in node:
            walk(node['not'])
        elif 'age_years' in node and _is_nonneg_int(node['age_years'][0]):
            found.append(node['age_years'][0])
            upper = node['age_years'][1]
            if upper is not None and _is_nonneg_int(upper):
                found.append(upper + 1)

    walk(scope)
    return found


def _eval_node(node, ctx, exceptions, seen):
    """Evaluate a compiled scope expression. Missing/cyclic references fail
    closed: their containing card cannot be trusted, so the result is False
    and the structural problem is reported separately."""
    if 'all' in node:
        return all(_eval_node(item, ctx, exceptions, seen) for item in node['all'])
    if 'any' in node:
        return any(_eval_node(item, ctx, exceptions, seen) for item in node['any'])
    if 'not' in node:
        return not _eval_node(node['not'], ctx, exceptions, seen)
    if 'except' in node:
        name = node['except']
        if name not in exceptions or name in seen:
            return False
        return _eval_node(exceptions[name], ctx, exceptions, seen | {name})
    ok = True
    if 'market' in node:
        ok = ok and ctx['market'] == node['market']
    if 'form' in node:
        ok = ok and ctx['form'] == node['form']
    if 'age_years' in node:
        lo, hi = node['age_years']
        ok = ok and ctx['age'] >= lo and (hi is None or ctx['age'] <= hi)
    return ok


def _refs(node):
    out = []
    if not isinstance(node, dict):
        return out
    if 'all' in node:
        for item in node['all']:
            out.extend(_refs(item))
    elif 'any' in node:
        for item in node['any']:
            out.extend(_refs(item))
    elif 'not' in node:
        out.extend(_refs(node['not']))
    elif 'except' in node:
        out.append(node['except'])
    return out


def _exception_issues(exceptions, card_scopes):
    """Undefined references and reference cycles, per section and language.

    References are collected from every card scope and every defined
    exception, so a name only mentioned in a card (never defined) is still
    caught.
    """
    issues = []
    keys = set(exceptions) | {(lang, section) for lang, section, _ in card_scopes}
    for key in sorted(keys):
        language, section = key
        table = exceptions.get(key, {})
        referenced = set()
        for _, scope in table.items():
            referenced.update(_refs(scope))
        for lang, sec, scope in card_scopes:
            if (lang, sec) == key:
                referenced.update(_refs(scope))
        for name in sorted(referenced):
            if name not in table:
                issues.append({'language': language, 'section': section, 'name': name,
                               'reason': 'undefined_exception_reference'})

        reported_cycle = set()
        for root in table:
            stack = [(root, [root])]
            while stack:
                name, path = stack.pop()
                for target in _refs(table[name]):
                    if target not in table:
                        continue  # already reported as undefined
                    if target in path and target not in reported_cycle:
                        reported_cycle.add(target)
                        issues.append({'language': language, 'section': section,
                                       'name': target, 'reason': 'exception_cycle'})
                    elif target not in path:
                        stack.append((target, path + [target]))
    # Deduplicate while preserving order.
    unique = []
    seen = set()
    for issue in issues:
        marker = json.dumps(issue, sort_keys=True)
        if marker not in seen:
            seen.add(marker)
            unique.append(issue)
    return unique


def load_inputs(cards, exceptions, authorized_markets, authorized_form):
    """Group raw card rows by language/section, compile scopes and exceptions.

    ``cards`` are rule_card rows joined with evidence event_time; only rows
    visible at the adjudication instant are passed in. Within one
    (language, section, rule_key) the latest card supersedes earlier ones --
    rule keys label amendments, they never determine equivalence themselves.
    """
    grouped = {}
    for card in sorted(cards, key=lambda c: (c['event_time'], c['event_id'])):
        section = grouped.setdefault((card['language'], card['section_id']), [])
        section.append(card)
    selected = {}
    for key, section_cards in grouped.items():
        kept = {}
        for card in section_cards:
            kept[card['rule_key']] = card
        selected[key] = list(kept.values())

    parsed = {}
    problems = []
    for key, section_cards in selected.items():
        for card in section_cards:
            try:
                scope = json.loads(card['scope_json'])
            except (TypeError, ValueError):
                problems.append({'language': card['language'], 'section': card['section_id'],
                                 'rule_key': card['rule_key'], 'reason': 'scope_json_not_parseable'})
                continue
            for problem in validate_scope(scope):
                problems.append({'language': card['language'], 'section': card['section_id'],
                                 'rule_key': card['rule_key'], **problem})
            parsed[card['event_id']] = scope

    exc_tables = {}
    for row in exceptions:
        key = (row['language'], row['section_id'])
        table = exc_tables.setdefault(key, {})
        if row['name'] in table:
            problems.append({'language': row['language'], 'section': row['section_id'],
                             'name': row['name'], 'reason': 'duplicate_exception_definition'})
            continue
        try:
            scope = json.loads(row['scope_json'])
        except (TypeError, ValueError):
            problems.append({'language': row['language'], 'section': row['section_id'],
                             'name': row['name'], 'reason': 'scope_json_not_parseable'})
            continue
        for problem in validate_scope(scope):
            problems.append({'language': row['language'], 'section': row['section_id'],
                             'name': row['name'], **problem})
        table[row['name']] = scope

    unknown = set()
    for scope in list(parsed.values()) + [s for table in exc_tables.values() for s in table.values()]:
        def collect(node):
            if not isinstance(node, dict):
                return
            if 'all' in node:
                for item in node['all']:
                    collect(item)
            elif 'any' in node:
                for item in node['any']:
                    collect(item)
            elif 'not' in node:
                collect(node['not'])
            else:
                if 'market' in node and node['market'] not in authorized_markets:
                    unknown.add(('market', node['market']))
                if 'form' in node and node['form'] != authorized_form:
                    unknown.add(('form', node['form']))
        collect(scope)
    for kind, value in sorted(unknown):
        problems.append({'reason': 'unknown_' + kind + '_not_in_authorized_set', 'value': value})

    card_scopes = [(card['language'], card['section_id'], parsed[card['event_id']])
                   for key, section_cards in selected.items() for card in section_cards
                   if card['event_id'] in parsed]
    return selected, parsed, exc_tables, problems, card_scopes


def _age_segments(bounds):
    """Cut the non-negative integer age axis at every bound; last is open."""
    cuts = sorted({0} | set(bounds))
    segments = []
    for i, lo in enumerate(cuts):
        hi = None if i == len(cuts) - 1 else cuts[i + 1] - 1
        segments.append((lo, hi))
    return segments


def _actions_at(selected, parsed, exc_tables, language, section, market, form, age):
    actions = {}
    for card in selected.get((language, section), ()):
        scope = parsed.get(card['event_id'])
        if scope is None:
            continue
        ctx = {'market': market, 'form': form, 'age': age}
        if _eval_node(scope, ctx, exc_tables.get((language, section), {}), set()):
            actions.setdefault(card['action'], []).append(card['rule_key'])
    return actions


def compare(cards, exceptions, authorized_markets, authorized_form, domain_markets):
    """Compare source vs translated action functions over the whole domain.

    Returns a certification dict with decision ``equivalent`` / ``different``
    / ``pending``. ``different`` carries replayable difference witnesses
    (concrete market + age band where the two action functions disagree);
    ``pending`` carries the structural evidence requiring human review.
    """
    selected, parsed, exc_tables, problems, card_scopes = load_inputs(
        cards, exceptions, authorized_markets, authorized_form)
    problems.extend(_exception_issues(exc_tables, card_scopes))

    sections = sorted({section for (language, section) in selected})
    trans_lang = next((lang for (lang, _) in selected if lang != 'source'), None)
    bounds = [0]
    for scope in parsed.values():
        bounds.extend(_age_bounds(scope))
    for table in exc_tables.values():
        for scope in table.values():
            bounds.extend(_age_bounds(scope))
    segments = _age_segments(bounds)

    witnesses = list(problems)
    if not problems:
        for market in domain_markets:
            for lo, hi in segments:
                for section in sections:
                    src = _actions_at(selected, parsed, exc_tables, 'source', section,
                                      market, authorized_form, lo)
                    tr = _actions_at(selected, parsed, exc_tables, trans_lang, section,
                                     market, authorized_form, lo) if trans_lang else {}
                    if len(src) > 1:
                        witnesses.append({'type': 'conflicting_actions', 'side': 'source',
                                          'section': section, 'market': market,
                                          'age_years': [lo, hi], 'actions': sorted(src)})
                    if len(tr) > 1:
                        witnesses.append({'type': 'conflicting_actions', 'side': 'translated',
                                          'section': section, 'market': market,
                                          'age_years': [lo, hi], 'actions': sorted(tr)})
                    if set(src) != set(tr):
                        witnesses.append({'type': 'action_mismatch', 'section': section,
                                          'market': market, 'form': authorized_form,
                                          'age_years': [lo, hi],
                                          'source_actions': sorted(src),
                                          'translated_actions': sorted(tr)})
    if problems:
        decision, reason = 'pending', 'structural_evidence_under_review'
    elif any(w['type'] == 'conflicting_actions' for w in witnesses):
        decision, reason = 'pending', 'conflicting_actions_overlap'
    elif witnesses:
        decision, reason = 'different', 'action_functions_differ'
    else:
        decision, reason = 'equivalent', 'action_functions_equal_over_domain'
    if decision == 'equivalent':
        witnesses = [
            {'market': market, 'form': authorized_form, 'section': section,
             'age_years': [lo, hi],
             'actions': sorted(_actions_at(selected, parsed, exc_tables, 'source', section,
                                           market, authorized_form, lo))}
            for market in domain_markets for lo, hi in segments for section in sections]
    return {'decision': decision, 'reason': reason, 'witnesses': witnesses,
            'segments': segments, 'sections': sections,
            'selected': selected, 'parsed': parsed, 'exceptions': exc_tables}


def card_summary(card):
    return {'event_id': card['event_id'], 'section_id': card['section_id'],
            'rule_key': card['rule_key'], 'action': card['action'],
            'scope': json.loads(card['scope_json'])}


def input_hash(adjudicated_at, approval_no, edition_no, language, receiver_type,
               receiver_id, domain_markets, cards, exceptions):
    payload = [adjudicated_at, approval_no, str(edition_no), language, receiver_type,
               receiver_id, sorted(domain_markets),
               [(c['event_id'], c['event_time']) for c in cards],
               [(e['event_id'], e['event_time']) for e in exceptions]]
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')
                      ).encode('utf-8')
    return hashlib.sha256(body).hexdigest()
