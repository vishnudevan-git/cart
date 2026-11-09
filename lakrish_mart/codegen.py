"""Short, unique, human-readable codes (supplier codes, category codes).

Used by the models' save() and by the data migrations that backfill existing
rows, so both always produce the same result.

    unique_code('NutriHub Traders', 4, taken)   ->  'NUTR'
    unique_code('Nuts Traders 2',   4, taken)   ->  'NUT2'   (NUTS was taken)
    unique_code('Organic Products', 3, taken, alternatives=[...])
"""
import re


def alnum(text):
    """Upper-case letters and digits only: 'Nuts & Co.' -> 'NUTSCO'."""
    return re.sub(r'[^A-Za-z0-9]', '', text or '').upper()


def initials_code(name, length):
    """Word initials, topped up with the next letters of the first word.

    'Organic Products', 3 -> 'OPR'.  Returns '' for a single-word name
    (there is nothing to take initials from).
    """
    words = [alnum(w) for w in (name or '').split() if alnum(w)]
    if len(words) < 2:
        return ''
    initials = ''.join(w[0] for w in words)[:length]
    fill = words[0][1:]
    return (initials + fill)[:length].ljust(length, 'X')


def unique_code(name, length, is_taken, alternatives=()):
    """First free code of exactly `length` characters for `name`.

    Order tried: the first `length` letters of the name, then each of
    `alternatives`, then the same prefix shortened with a number added
    (NUT2, NUT3 ... NU10 ...). `is_taken(code)` says whether a code is used.
    """
    base = alnum(name).ljust(length, 'X')[:length]
    for cand in (base, *alternatives):
        cand = (cand or '')[:length]
        if len(cand) == length and not is_taken(cand):
            return cand
    for digits in range(1, length):
        keep = length - digits
        start = 2 if digits == 1 else 10 ** (digits - 1)
        for n in range(start, 10 ** digits):
            cand = base[:keep] + str(n).zfill(digits)
            if not is_taken(cand):
                return cand
    raise ValueError(f'No free {length}-character code left for {name!r}')
