"""
Compiles .po files to .mo files.
Uses Babel to parse .po, then writes a clean .mo with proper UTF-8 header.
Run: python compile_messages.py
"""
import os
import struct
from babel.messages.pofile import read_po


def write_mo(fileobj, catalog):
    """Write a proper GNU .mo file with UTF-8 charset header."""
    messages = {}

    # Always include the metadata header as empty-string key
    # Must use real newline characters
    messages[''] = (
        'Content-Type: text/plain; charset=UTF-8\n'
        'Content-Transfer-Encoding: 8bit\n'
    )

    for msg in catalog:
        if msg.id and msg.string:
            if isinstance(msg.id, str):
                # Unescape literal \n sequences from .po parsing
                val = msg.string.replace('\\n', '\n') if '\\n' in msg.string else msg.string
                messages[msg.id] = val

    keys = sorted(messages.keys())
    values = [messages[k] for k in keys]

    keys_b = [k.encode('utf-8') for k in keys]
    vals_b = [v.encode('utf-8') for v in values]

    n = len(keys)
    key_table_offset = 28
    val_table_offset = key_table_offset + n * 8
    key_data_offset  = val_table_offset + n * 8
    val_data_offset  = key_data_offset  + sum(len(k) + 1 for k in keys_b)

    fileobj.write(struct.pack('<I', 0x950412de))
    fileobj.write(struct.pack('<I', 0))
    fileobj.write(struct.pack('<I', n))
    fileobj.write(struct.pack('<I', key_table_offset))
    fileobj.write(struct.pack('<I', val_table_offset))
    fileobj.write(struct.pack('<I', 0))
    fileobj.write(struct.pack('<I', 0))

    pos = key_data_offset
    for kb in keys_b:
        fileobj.write(struct.pack('<II', len(kb), pos))
        pos += len(kb) + 1

    pos = val_data_offset
    for vb in vals_b:
        fileobj.write(struct.pack('<II', len(vb), pos))
        pos += len(vb) + 1

    for kb in keys_b:
        fileobj.write(kb + b'\x00')

    for vb in vals_b:
        fileobj.write(vb + b'\x00')


base = 'locale'
for lang in ['ta', 'te', 'kn', 'hi', 'fr']:
    po_path = os.path.join(base, lang, 'LC_MESSAGES', 'django.po')
    mo_path = os.path.join(base, lang, 'LC_MESSAGES', 'django.mo')
    if os.path.exists(po_path):
        with open(po_path, 'rb') as f:
            catalog = read_po(f, locale=lang, charset='utf-8')
        with open(mo_path, 'wb') as f:
            write_mo(f, catalog)
        print(f'Compiled: {mo_path}  ({len(catalog)} messages)')

print('Done.')
