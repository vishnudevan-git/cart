import glob

OLD = "  <a class=\"nav-link\" href=\"{% url 'admin_panel:reports' %}\"><i class=\"bi bi-bar-chart\"></i> Reports</a>"
NEW = """  <a class="nav-link" href="{% url 'admin_panel:reports' %}"><i class="bi bi-bar-chart"></i> Reports</a>
  <a class="nav-link" href="{% url 'admin_panel:stockist_create' %}"><i class="bi bi-boxes"></i> Stockist</a>"""

count = 0
for path in glob.glob('templates/admin_panel/*.html'):
    with open(path, encoding='utf-8') as f:
        content = f.read()
    if OLD in content and 'stockist_create' not in content:
        content = content.replace(OLD, NEW)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        count += 1
        print(f'Updated: {path}')

print(f'Done: {count} files updated')
