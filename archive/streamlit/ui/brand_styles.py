"""Brand portal look — sharp, ad-forward analytics."""

BRAND_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=IBM+Plex+Sans:wght@400;600&display=swap');
.stApp {
  background:
    radial-gradient(1000px 480px at 100% -10%, #FDE68A 0%, transparent 50%),
    radial-gradient(900px 400px at 0% 0%, #BBF7D0 0%, transparent 45%),
    linear-gradient(180deg, #F8FAFC 0%, #EFF6FF 100%);
}
.brand-title {
  font-family: 'Space Grotesk', sans-serif;
  font-size: 2rem;
  font-weight: 700;
  color: #0F172A;
  margin-bottom: 0.15rem;
}
.brand-sub { color: #475569; margin-bottom: 1rem; }
.ad-strip {
  background: linear-gradient(100deg, #0F172A, #166534 60%, #F59E0B);
  color: white;
  border-radius: 16px;
  padding: 1rem 1.25rem;
  margin-bottom: 1rem;
}
.login-shell {
  max-width: 440px;
  margin: 2rem auto;
  background: rgba(255,255,255,0.95);
  border-radius: 18px;
  padding: 1.5rem;
  border: 1px solid #E2E8F0;
}
div[data-testid="stMetric"] {
  background: rgba(255,255,255,0.85);
  border: 1px solid #E2E8F0;
  border-radius: 12px;
  padding: 0.7rem 0.85rem;
}
</style>
"""


def inject_brand(st):
    st.markdown(BRAND_CSS, unsafe_allow_html=True)
