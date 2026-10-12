"""Customer portal look — warm, personal, shoppable."""

CUSTOMER_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Manrope:wght@400;600;700&display=swap');
.stApp {
  background:
    radial-gradient(900px 420px at 0% 0%, #FFD8B5 0%, transparent 55%),
    radial-gradient(800px 380px at 100% 10%, #B8F1E0 0%, transparent 50%),
    linear-gradient(180deg, #FFF8F1 0%, #F7F3EE 100%);
}
.cust-brand {
  font-family: 'Fraunces', serif;
  font-size: 2.2rem;
  font-weight: 700;
  color: #1C1917;
  margin-bottom: 0.2rem;
}
.cust-sub { color: #57534E; font-family: 'Manrope', sans-serif; margin-bottom: 1rem; }
.merch-card {
  background: rgba(255,255,255,0.88);
  border: 1px solid #E7E5E4;
  border-radius: 18px;
  padding: 1rem 1.1rem;
  margin-bottom: 0.75rem;
  box-shadow: 0 8px 24px rgba(28,25,23,0.06);
}
.merch-card h4 {
  font-family: 'Fraunces', serif;
  margin: 0 0 0.35rem 0;
  color: #1C1917;
}
.score-pill {
  display: inline-block;
  background: #14532D;
  color: #ECFDF5;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 0.78rem;
  font-weight: 700;
  font-family: 'Manrope', sans-serif;
}
.login-shell {
  max-width: 420px;
  margin: 2rem auto;
  background: rgba(255,255,255,0.92);
  border-radius: 20px;
  padding: 1.5rem;
  border: 1px solid #E7E5E4;
}
</style>
"""


def inject_customer(st):
    st.markdown(CUSTOMER_CSS, unsafe_allow_html=True)
