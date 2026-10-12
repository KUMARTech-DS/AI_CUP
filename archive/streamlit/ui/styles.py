"""Dashboard visual language — ad-forward, energetic, not purple-default."""

DASHBOARD_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700;800&family=IBM+Plex+Sans:wght@400;500;600&display=swap');

html, body, [class*="css"] {
  font-family: 'IBM Plex Sans', sans-serif;
}
.stApp {
  background:
    radial-gradient(1200px 500px at 10% -10%, #FFE7C7 0%, transparent 55%),
    radial-gradient(900px 400px at 100% 0%, #C8F5E4 0%, transparent 50%),
    linear-gradient(180deg, #FFF9F2 0%, #F4F7FB 40%, #EEF2F7 100%);
}
.hero-brand {
  font-family: 'Outfit', sans-serif;
  font-size: 2.4rem;
  font-weight: 800;
  letter-spacing: -0.03em;
  color: #0B1F33;
  margin-bottom: 0.15rem;
}
.hero-sub {
  color: #4B5C6B;
  font-size: 1.05rem;
  max-width: 720px;
  margin-bottom: 1rem;
}
.ad-banner {
  background: linear-gradient(105deg, #0B1F33 0%, #14532D 55%, #F59E0B 140%);
  color: #fff;
  border-radius: 18px;
  padding: 1.1rem 1.4rem;
  margin: 0.6rem 0 1.2rem 0;
  box-shadow: 0 12px 30px rgba(11, 31, 51, 0.18);
}
.ad-banner h3 {
  font-family: 'Outfit', sans-serif;
  margin: 0 0 0.25rem 0;
  font-size: 1.25rem;
}
.ad-banner p { margin: 0; opacity: 0.92; }
.kpi-chip {
  background: rgba(255,255,255,0.75);
  border: 1px solid #E5E7EB;
  border-radius: 14px;
  padding: 0.85rem 1rem;
}
.metric-badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 0.8rem;
  font-weight: 600;
}
.badge-success { background: #DCFCE7; color: #166534; }
.badge-warning { background: #FEF3C7; color: #92400E; }
.badge-error { background: #FEE2E2; color: #991B1B; }
div[data-testid="stMetric"] {
  background: rgba(255,255,255,0.8);
  border: 1px solid #E5E7EB;
  border-radius: 14px;
  padding: 0.75rem 0.9rem;
}
.stTabs [data-baseweb="tab-list"] { gap: 6px; }
.stTabs [data-baseweb="tab"] {
  background: rgba(255,255,255,0.7);
  border-radius: 999px;
  padding: 8px 16px;
  font-weight: 600;
}
</style>
"""


def inject(st):
    st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)
