# GameBot
Posta notícias de games (RSS) e promoções da Steam num canal do Discord, 100% grátis via GitHub Actions + Webhook.  
Passo a passo completo: Criar webhook no Discord -> criar repositório no GitHub -> subir estes arquivos -> adicionar o secret DISCORD_WEBHOOK_URL -> rodar o workflow manualmente (Actions > GameBot > Run workflow).  
Teste local: pip install -r requirements.txt && DISCORD_WEBHOOK_URL=... python bot.py news
