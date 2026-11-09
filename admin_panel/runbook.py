from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.urls import URLPattern, URLResolver, get_resolver

LOCAL_APPS = {
    'accounts',
    'admin_panel',
    'cart',
    'delivery',
    'distributors',
    'orders',
    'payments',
    'products',
    'stockist',
    'suppliers',
    'support',
    'transactions',
}


def _logic_catalog():
    return [
        {
            'title': 'Role Access Gate',
            'rules': [
                'All role dashboards require authenticated users with matching roles.',
                'Active role is stored in session and drives role-specific navigation.',
                'Admin-only lifecycle pages require admin role or superuser.',
            ],
        },
        {
            'title': 'Product Lifecycle',
            'rules': [
                'Supplier creates products; admin approves or rejects.',
                'Soft-delete marks product as deleted instead of hard removal.',
                'Approved products can be listed by distributors for customers.',
            ],
        },
        {
            'title': 'Order and Payment Flow',
            'rules': [
                'Checkout computes distributor-specific prices and delivery rules.',
                'Order and item transaction events are logged for audit trail.',
                'Payment and settlement records sync with order lifecycle updates.',
            ],
        },
        {
            'title': 'Demo Safety Controls',
            'rules': [
                'Demo quick-login is controlled by SiteSettings toggles.',
                'Demo sessions are marked in session keys and isolated by cookie names.',
                'Demo transaction guard blocks write actions for orders, payments, and transactions.',
            ],
        },
    ]


def _ai_collab_notes():
    return {
        'headline': 'Guidance for Gemini Validation and Implementation',
        'summary': (
            'Model mapping from products/distributors is correct. Use immediate stale-flag updates '
            'plus daily reconciliation for reliability.'
        ),
        'validated_strengths': [
            'Blocking checkout for stale prices while showing distributor warning badges balances visibility and margin protection.',
            'Stale flag clears only on explicit distributor action, preventing accidental bypass.',
            'Resolution metadata preserves operational audit quality.',
        ],
        'recommendation': {
            'title': 'Stale Price Lifecycle Strategy',
            'items': [
                'Primary mechanism: event-driven stale flag update when supplier base price changes.',
                'Safety mechanism: scheduled daily reconciliation command to repair any missed stale flags.',
                'Keep stale entries visible with warning badge; block checkout until repriced/accepted.',
                'Clear stale flag only when distributor confirms new price or reprices variant.',
            ],
        },
        'targeted_questions': [
            {
                'title': '1. Decoupling post_save Logic',
                'scenario': (
                    'Bulk supplier updates (for example CSV import) can bypass standard Django post_save behavior when bulk_update is used.'
                ),
                'decision': (
                    'Use a shared stale-marking service called by both post_save and bulk import service layers. '
                    'Do not rely on signal-only behavior.'
                ),
            },
            {
                'title': '2. Cart Cache Invalidation Strategy',
                'scenario': (
                    'Customer should be blocked from checkout if stale pricing appears while browsing.'
                ),
                'decision': (
                    'Enforce synchronous stale checks on cart page load and checkout submit as mandatory gates. '
                    'Async cart refresh alerts are optional UX enhancement.'
                ),
            },
            {
                'title': '3. Daily Reconciliation Drift Bounds',
                'scenario': (
                    'Daily command must define drift and repair missed stale flags consistently.'
                ),
                'decision': (
                    'Use direct value/state comparison as primary drift rule (distributor snapshot out of sync with supplier price). '
                    'Do not auto-clear stale in reconciliation; clearing remains explicit distributor action.'
                ),
            },
            {
                'title': '4. Retroactive Margin Check on Repricing',
                'scenario': (
                    'When a distributor clears the stale flag by confirming new price, should system validate '
                    'that their price maintains minimum profit margin over supplier base price?'
                ),
                'decision': (
                    'Yes, enforce mandatory margin validation on repricing to prevent accidental loss-making typos. '
                    'Distributor must maintain at least base price + 5% (configurable) or repricing is rejected with clear error. '
                    'This is a protective guardrail that catches input errors before margin damage occurs.'
                ),
            },
            {
                'title': '5. Stockist Inbound Visibility for Deleted Variants',
                'scenario': (
                    'When supplier soft-deletes a variant, it cascades to is_listed=False for distributors and clears carts. '
                    'Should stockists still see the variant in warehouse dashboards if they hold physical inventory for it?'
                ),
                'decision': (
                    'Yes, stockists retain read-only visibility of soft-deleted variants in their inventory/fulfillment dashboards '
                    'so they can process and return existing physical stock. Deleted variants appear with "DISCONTINUED" badge '
                    'but remain queryable for warehouse operations. This prevents inventory getting stuck or lost.'
                ),
            },
        ],
        'implementation_scope': [
            'Detect base-price change on ProductVariant save and mark related DistributorVariantPrice rows stale.',
            'Add management command for daily stale reconciliation across variants/prices.',
            'Align distributor UI warning and checkout guard behavior with stale flag semantics.',
            'Track resolution metadata (who/when) if operational audit is needed.',
        ],
        'implementation_order': [
            'Foundation (DONE): shared stale-marking domain service + signal integration + bulk upload integration + 5% margin guard.',
            'Protection: cart and checkout guard enforcement with clear stale messaging.',
            'Reliability: daily reconciliation command and summary reporting.',
            'UX polish: optional asynchronous cart refresh warning for stale transitions.',
        ],
        'acceptance_checks': [
            'Supplier edits base variant price -> linked distributor variant prices become stale immediately.',
            'Customer checkout rejects stale-priced items with clear next-step message.',
            'Distributor repricing/accept action clears stale flag and allows checkout again.',
            'Daily reconciliation reports repaired rows and exits cleanly when no drift exists.',
        ],
        'foundation_layer_status': {
            'status': 'COMPLETE',
            'completed_on': 'May 25, 2026',
            'changes': [
                {
                    'file': 'distributors/services.py',
                    'type': 'NEW FILE',
                    'what': 'Domain service layer with 3 functions',
                    'details': [
                        'mark_distributor_prices_stale(variant_id, new_supplier_price): marks all distributor prices stale when supplier changes price. Sets price_stale=True and stale_marked_at timestamp.',
                        'validate_margin_on_repricing(base_price, selling_price, min_margin_percent=5.0): blocks distributor from saving a price that earns less than 5% profit over their cost. Prevents accidental typos.',
                        'clear_stale_flag_on_repricing(dvp, force=False): clears the stale flag when distributor confirms a new price, but only after margin validation passes.',
                    ],
                },
                {
                    'file': 'distributors/models.py',
                    'type': 'MODIFIED',
                    'what': 'Added stale_marked_at field to DistributorVariantPrice',
                    'details': [
                        'New DateTimeField: stale_marked_at — records the exact moment a price was flagged stale.',
                        'Purpose: enables future daily reconciliation to detect drift (how long has this been stale?).',
                        'Migration 0022 applied to both prod and demo databases.',
                    ],
                },
                {
                    'file': 'products/models.py',
                    'type': 'MODIFIED',
                    'what': 'ProductVariant.save() now calls the domain service',
                    'details': [
                        'When supplier edits selling_price or mrp, ProductVariant.save() detects the change.',
                        'Calls mark_distributor_prices_stale() from the service layer (not inline code).',
                        'Falls back to direct DB update if service is unavailable — no crashes.',
                    ],
                },
                {
                    'file': 'suppliers/views.py',
                    'type': 'MODIFIED',
                    'what': 'bulk_upload() now marks prices stale after creating variants',
                    'details': [
                        'Previously: bulk Excel import created variants without triggering stale flags.',
                        'Now: after creating each ProductVariant row, service is called immediately.',
                        'Prevents bulk imports from bypassing the stale-flag lifecycle.',
                    ],
                },
                {
                    'file': 'distributors/views.py',
                    'type': 'MODIFIED',
                    'what': 'save_product_listing() validates 5% margin before saving any price',
                    'details': [
                        'Before saving prices, calls validate_margin_on_repricing() for every variant.',
                        'If ANY variant fails the 5% minimum margin, NONE are saved — all or nothing.',
                        'Shows clear error: "Minimum price required: ₹X (you tried ₹Y)".',
                        'Clears price_stale=False and new_supplier_price=None on success.',
                    ],
                },
            ],
            'tests_passed': [
                'Valid price with 5% margin -> Accepted',
                'Price with only 2% margin -> Blocked with error message',
                'Zero base price -> Blocked (edge case)',
                'Negative base price -> Blocked (edge case)',
                'String decimal inputs -> Handled correctly',
                'Service function imports and callable -> Verified',
            ],
        },
    }


def _route_summary(routes):
    module_counts = {}
    for route in routes:
        fragment = route['route'].lstrip('/').split('/', 1)[0] or 'root'
        module_counts[fragment] = module_counts.get(fragment, 0) + 1
    summary = [{'module': key, 'count': value} for key, value in sorted(module_counts.items())]
    return {
        'total_routes': len(routes),
        'module_counts': summary,
    }


def _flatten_patterns(patterns, prefix=''):
    flattened = []
    for entry in patterns:
        if isinstance(entry, URLPattern):
            route = f"/{prefix}{entry.pattern}".replace('^', '').replace('$', '')
            route = route.replace('//', '/')
            flattened.append({
                'route': route,
                'name': entry.name or '-',
                'lookup': entry.lookup_str,
            })
        elif isinstance(entry, URLResolver):
            child_prefix = f"{prefix}{entry.pattern}"
            flattened.extend(_flatten_patterns(entry.url_patterns, child_prefix))
    return flattened


def _find_reference_files(base_dir):
    files = []
    for pattern in ('*.md', '*.html', '*.txt'):
        for path in sorted(base_dir.glob(pattern)):
            if path.name.startswith('.'):
                continue
            files.append(path.name)
    return files[:80]


def collect_runbook_data():
    resolver = get_resolver()
    routes = _flatten_patterns(resolver.url_patterns)
    routes = sorted(routes, key=lambda item: item['route'])
    route_summary = _route_summary(routes)

    app_models = []
    for app_label in sorted(LOCAL_APPS):
        try:
            cfg = apps.get_app_config(app_label)
        except LookupError:
            continue
        models = []
        for model in cfg.get_models():
            models.append({
                'name': model.__name__,
                'table': model._meta.db_table,
                'fields': len(model._meta.fields),
            })
        app_models.append({
            'app_label': app_label,
            'models': sorted(models, key=lambda item: item['name']),
        })

    base_dir = Path(settings.BASE_DIR)
    references = _find_reference_files(base_dir)

    demo_base_url = getattr(settings, 'DEMO_BASE_URL', 'http://127.0.0.1:8001').rstrip('/')
    prod_base_url = getattr(settings, 'PROD_BASE_URL', 'http://127.0.0.1:8888').rstrip('/')
    show_url_map = getattr(settings, 'SHOW_RUNBOOK_URL_MAP', False)
    demo_links = [
        {
            'label': 'Customer Demo',
            'url': f'{demo_base_url}/accounts/demo-login/?role=customer&next=/'
        },
        {
            'label': 'Distributor Demo',
            'url': f'{demo_base_url}/accounts/demo-login/?role=distributor&next=/distributor/dashboard/'
        },
        {
            'label': 'Supplier Demo',
            'url': f'{demo_base_url}/accounts/demo-login/?role=supplier&next=/supplier/dashboard/'
        },
        {
            'label': 'Stockist Demo',
            'url': f'{demo_base_url}/accounts/demo-login/?role=stockist&next=/stockist/dashboard/'
        },
    ]

    return {
        'project_name': 'LaKrish Mart',
        'routes': routes,
        'route_summary': route_summary,
        'show_url_map': show_url_map,
        'app_models': app_models,
        'logic_sections': _logic_catalog(),
        'ai_collab': _ai_collab_notes(),
        'references': references,
        'demo_links': demo_links,
        'prod_base_url': prod_base_url,
        'demo_base_url': demo_base_url,
        'base_dir': str(base_dir),
    }
