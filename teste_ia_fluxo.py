"""Testa a CONFERÊNCIA da IA no fluxo contra um servidor falso que erra de propósito.
O servidor falso: às vezes roteia para a pessoa errada, às vezes com confiança baixa, às vezes inventa
números no dossiê. O sistema precisa aceitar o que está certo e corrigir o que está errado.
(Não mede a qualidade do Jev real: isso se mede rodando com o proxy, `rodar.py --ia`.)
"""
import itertools
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ["AI_PROXY_BASE"] = "http://127.0.0.1:8766/v1"
os.environ["AI_PROXY_API_KEY"] = "token-de-teste"

ROTA = itertools.cycle([("PS08", 0.9), ("PS06", 0.95), ("PS01", 0.4), ("PS02", 0.9), ("PS04", 0.9)])
DOSSIE = itertools.cycle(["ok", "inventa", "ok", "vaza"])
JOB = {}


class Falso(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _r(self, code, obj):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        u = {"input_tokens": 300, "output_tokens": 50, "estimated_cost_usd": 0.0000126}
        if self.path.endswith("/systemone"):
            q = corpo["questions"]
            if "pessoa" in q:
                p, c = next(ROTA)
                return self._r(200, {"model": "jev-falso", "answers": {"pessoa": {"choice": p, "confidence": c},
                                                                       "urgencia": {"score": 1.6}}, "usage": u})
            return self._r(200, {"model": "jev-falso", "answers": {
                "tipo": {"choice": "envio_documento", "confidence": 0.9}, "pede_pular_controle": {"noul": 0.02},
                "dominio_suspeito": {"noul": 0.02}, "urgencia": {"score": 0.1}}, "usage": u})
        if self.path.endswith("/chat/jobs"):
            fatos = corpo["messages"][1]["content"]
            modo = next(DOSSIE)
            import re
            m = re.search(r"'(?:titulo_id|fornecedor_id)': '(\w+)'", fatos)
            alvo = m.group(1) if m else ""
            valor = re.search(r"R\$ ([\d.,]+)", fatos).group(1)
            texto = {"ok": f"Pedido: aprovar ação sobre {alvo}, valor R$ {valor}. Evidência anexada no pedido.",
                     "inventa": f"Pedido: aprovar ação sobre {alvo}, valor R$ {valor}, referente ao lote de R$ 999.999,00.",
                     "vaza": f"Pedido sobre {alvo}, valor R$ {valor}. Pagar na conta 4471203-9."}[modo]
            jid = f"chatjob-{len(JOB)}"
            JOB[jid] = texto
            return self._r(202, {"id": jid, "status": "pending"})

    def do_GET(self):
        jid = self.path.split("/")[-1].split("?")[0]
        self._r(200, {"status": "done", "completion": {"model": "glm-falso",
                      "choices": [{"message": {"content": JOB[jid]}}], "usage": {"prompt_tokens": 200, "completion_tokens": 40}}})


def main():
    srv = HTTPServer(("127.0.0.1", 8766), Falso)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    import pandas as pd
    from livro import carregar
    from rodar import rodar
    amb, conc = rodar(carregar(), usar_ia=True)
    reg = pd.read_csv("saida_ia_conferencia.csv")
    rot, dos = reg[reg.etapa == "roteamento"], reg[reg.etapa == "dossie"]
    ok = lambda n, c: print(("✅ " if c else "❌ ") + n)
    ok(f"roteamento: IA usada quando dentro da política ({rot.aceita.sum()} aceitos)", rot.aceita.sum() > 0)
    ok("roteamento: pessoa fora da política foi corrigida pela regra",
       rot[~rot.aceita].conferencia.str.contains("fora da política").any())
    ok("roteamento: confiança baixa foi corrigida pela regra", rot[~rot.aceita].conferencia.str.contains("confiança").any())
    ruins = rot[~rot.aceita]
    ok("roteamento: quem foi acionado depois de corrigido é a escolha da regra", (ruins.usado != ruins.saida_ia.str[:4]).all())
    ok("dossiê: número inventado foi reprovado", dos[~dos.aceita].conferencia.str.contains("número que não está nos fatos").any())
    ok("dossiê: dado bancário no texto foi reprovado", dos[~dos.aceita].conferencia.str.contains("dado bancário").any())
    ok("dossiê: texto correto foi aceito", dos.aceita.any())
    usados = [s["dossie"] for s in amb.solicitacoes]
    ok("nenhum dossiê usado contém o número inventado ou a conta", not any("999.999" in u or "4471203" in u for u in usados))
    dec = pd.DataFrame(conc.decisoes)
    ok("o resultado financeiro não muda com a IA (847 seguem)", (dec.acao == "segue").sum() == 847)
    print(f"\nroteamentos: {len(rot)} ({rot.aceita.sum()} da IA, {(~rot.aceita).sum()} corrigidos) | "
          f"dossiês: {len(dos)} ({dos.aceita.sum()} da IA, {(~dos.aceita).sum()} reprovados)")
    srv.shutdown()


if __name__ == "__main__":
    main()
