def premium_ui_css() -> str:
    return """
            :root {
                --bg: #f5f1e8;
                --bg-accent: radial-gradient(circle at top left, rgba(173, 137, 61, 0.16), transparent 28%),
                             radial-gradient(circle at right center, rgba(15, 32, 58, 0.08), transparent 24%),
                             linear-gradient(180deg, #f7f3eb 0%, #efe8dc 100%);
                --surface: rgba(255, 252, 246, 0.88);
                --surface-strong: #fffdf8;
                --surface-dark: #10233c;
                --ink: #142032;
                --muted: #5a6778;
                --line: rgba(16, 35, 60, 0.10);
                --line-strong: rgba(16, 35, 60, 0.18);
                --brand: #10233c;
                --brand-soft: #d7e0ec;
                --accent: #b68a33;
                --accent-soft: #f4e5ba;
                --success: #0e6a44;
                --success-soft: #dff4e8;
                --warning: #8a5d12;
                --warning-soft: #fbefcf;
                --danger: #8a2f2f;
                --danger-soft: #f9dddd;
                --shadow-soft: 0 18px 50px rgba(20, 32, 50, 0.08);
                --shadow-card: 0 14px 30px rgba(20, 32, 50, 0.08);
                --radius-xl: 24px;
                --radius-lg: 18px;
                --radius-md: 12px;
                --radius-sm: 10px;
            }
            * {
                box-sizing: border-box;
            }
            body {
                font-family: Georgia, "Times New Roman", serif;
                background: var(--bg-accent);
                color: var(--ink);
                margin: 0;
                padding: 32px 22px 56px;
                line-height: 1.5;
            }
            .container {
                max-width: 1180px;
                margin: 0 auto;
            }
            .hero {
                position: relative;
                overflow: hidden;
                background: linear-gradient(135deg, rgba(16, 35, 60, 0.97), rgba(26, 53, 87, 0.94));
                color: #f8f3ea;
                border-radius: 28px;
                padding: 30px 30px 26px;
                margin-bottom: 22px;
                box-shadow: 0 24px 70px rgba(16, 35, 60, 0.24);
            }
            .hero::after {
                content: "";
                position: absolute;
                inset: auto -8% -38% auto;
                width: 280px;
                height: 280px;
                background: radial-gradient(circle, rgba(182, 138, 51, 0.42), transparent 62%);
                pointer-events: none;
            }
            .eyebrow {
                display: inline-flex;
                align-items: center;
                gap: 8px;
                text-transform: uppercase;
                letter-spacing: 0.16em;
                font-size: 11px;
                font-weight: 700;
                color: #d9c79a;
                margin-bottom: 10px;
            }
            .hero h1 {
                margin: 0;
                font-size: clamp(34px, 5vw, 56px);
                line-height: 0.95;
                letter-spacing: -0.03em;
                max-width: 760px;
            }
            .hero p {
                max-width: 720px;
                margin: 14px 0 0;
                color: rgba(248, 243, 234, 0.82);
                font-size: 17px;
            }
            .hero-metrics {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                gap: 12px;
                margin-top: 22px;
            }
            .hero-metric {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 16px;
                padding: 14px 16px;
                backdrop-filter: blur(8px);
            }
            .hero-metric span {
                display: block;
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 0.10em;
                color: rgba(248, 243, 234, 0.66);
            }
            .hero-metric strong {
                display: block;
                margin-top: 6px;
                font-size: 24px;
                color: #fff8eb;
            }
            .top-menu {
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                margin-bottom: 18px;
                align-items: center;
            }
            .top-menu a {
                background: rgba(255, 253, 248, 0.72);
                color: var(--brand);
                border: 1px solid rgba(16, 35, 60, 0.08);
                border-radius: 999px;
                padding: 10px 14px;
                text-decoration: none;
                font-weight: 700;
                font-size: 13px;
                box-shadow: var(--shadow-card);
            }
            .top-menu a.active {
                background: var(--brand);
                color: #fff8ec;
                border-color: transparent;
            }
            .panel,
            .card,
            .filters,
            .metric,
            table {
                background: var(--surface);
                border: 1px solid var(--line);
                box-shadow: var(--shadow-card);
                backdrop-filter: blur(8px);
            }
            .panel,
            .card,
            .filters,
            .metric {
                border-radius: var(--radius-lg);
            }
            .card,
            .filters,
            .metric {
                padding: 18px;
            }
            .section-title {
                margin: 26px 0 12px;
                font-size: 24px;
                color: var(--brand);
                letter-spacing: -0.02em;
            }
            .lede {
                color: var(--muted);
                font-size: 15px;
                margin: 0 0 14px;
            }
            .grid-2 {
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 16px;
            }
            .grid-4 {
                display: grid;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                gap: 14px;
            }
            .summary {
                display: flex;
                gap: 10px;
                flex-wrap: wrap;
                margin-top: 14px;
            }
            .summary span {
                background: #f5efe3;
                border: 1px solid rgba(16, 35, 60, 0.08);
                border-radius: 999px;
                padding: 8px 12px;
                color: var(--muted);
                font-size: 13px;
            }
            .metric strong {
                display: block;
                font-size: 28px;
                line-height: 1;
                margin-top: 10px;
                color: var(--brand);
            }
            .metric small {
                display: block;
                margin-top: 6px;
                color: var(--muted);
            }
            .aviso {
                background: linear-gradient(135deg, var(--accent-soft), #f8efd7);
                color: #5d4312;
                border: 1px solid rgba(182, 138, 51, 0.22);
                padding: 15px 16px;
                border-radius: var(--radius-md);
                margin-bottom: 18px;
            }
            .filters form {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 12px;
                align-items: end;
            }
            .field {
                display: flex;
                flex-direction: column;
                gap: 7px;
            }
            label {
                font-size: 12px;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 0.08em;
                color: var(--muted);
            }
            input,
            select {
                width: 100%;
                min-width: 0;
                height: 44px;
                border: 1px solid var(--line-strong);
                border-radius: 12px;
                padding: 0 12px;
                font-size: 15px;
                background: rgba(255, 255, 255, 0.9);
                color: var(--ink);
            }
            button,
            .button-link {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                height: 44px;
                border: 0;
                border-radius: 999px;
                padding: 0 18px;
                background: linear-gradient(135deg, var(--brand), #21436f);
                color: #fff8ec;
                font-weight: 700;
                text-decoration: none;
                cursor: pointer;
                box-shadow: 0 14px 25px rgba(16, 35, 60, 0.16);
            }
            .button-link.secondary {
                background: #ece4d2;
                color: var(--brand);
                box-shadow: none;
            }
            .checkbox-row {
                display: flex;
                align-items: center;
                gap: 10px;
                min-height: 44px;
                flex-wrap: wrap;
            }
            .checkbox-row input {
                width: auto;
                height: auto;
            }
            .badge {
                display: inline-flex;
                align-items: center;
                border-radius: 999px;
                padding: 6px 10px;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0.04em;
                margin-bottom: 10px;
            }
            .badge-green {
                background: var(--success-soft);
                color: var(--success);
            }
            .badge-yellow {
                background: var(--warning-soft);
                color: var(--warning);
            }
            .badge-red {
                background: var(--danger-soft);
                color: var(--danger);
            }
            .bet-card {
                border-left: 5px solid #9aa8b7;
            }
            .bet-green {
                background: linear-gradient(180deg, rgba(223, 244, 232, 0.75), rgba(255, 252, 246, 0.92));
                border-left-color: var(--success);
            }
            .bet-yellow {
                background: linear-gradient(180deg, rgba(251, 239, 207, 0.88), rgba(255, 252, 246, 0.92));
                border-left-color: var(--warning);
            }
            .bet-red {
                background: linear-gradient(180deg, rgba(249, 221, 221, 0.82), rgba(255, 252, 246, 0.92));
                border-left-color: var(--danger);
            }
            table {
                width: 100%;
                border-collapse: collapse;
                overflow: hidden;
                border-radius: var(--radius-lg);
                margin-bottom: 26px;
            }
            th, td {
                padding: 12px 14px;
                border-bottom: 1px solid rgba(16, 35, 60, 0.08);
                text-align: left;
                font-size: 14px;
            }
            th {
                background: var(--surface-dark);
                color: #f8f3ea;
                font-size: 12px;
                text-transform: uppercase;
                letter-spacing: 0.08em;
            }
            tr:last-child td {
                border-bottom: 0;
            }
            .kpi-positive {
                color: var(--success);
            }
            .muted {
                color: var(--muted);
            }
            code {
                background: rgba(16, 35, 60, 0.08);
                border-radius: 8px;
                padding: 2px 6px;
            }
            @media (max-width: 900px) {
                body {
                    padding: 18px 14px 38px;
                }
                .grid-2,
                .grid-4 {
                    grid-template-columns: 1fr;
                }
                .hero {
                    padding: 22px 18px;
                }
                table {
                    display: block;
                    overflow-x: auto;
                }
            }
    """
