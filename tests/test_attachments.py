"""Validação de uploads, atomicidade e autorização de download."""

import unittest
from io import BytesIO

from support import AppCase

from sxs_core import ATTACHMENT_LIMIT


class AttachmentTests(AppCase):
    def test_bloqueia_extensao_conteudo_e_nome_invalidos(self) -> None:
        ticket_id = self.create_ticket(2)
        client, csrf = self.session_for(2)
        for name, body in (
            ("arquivo.svg", b"<svg/>"),
            ("imagem.png", b"conteudo falso"),
            ("relatorio.pdf", b"sem assinatura"),
            ("../arquivo.txt", b"texto"),
            ("dados.log", b"texto\x00binario"),
            ("arquivo.txt", b"x" * (ATTACHMENT_LIMIT + 1)),
        ):
            with self.subTest(name=name):
                response = client.post(
                    f"/chamados/{ticket_id}/anexos",
                    data={
                        "csrf_token": csrf,
                        "attachment": (BytesIO(body), name),
                    },
                )
                self.assertIn(response.status_code, {400, 413})
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 0)

    def test_anexo_valido_e_download_somente_para_dono(self) -> None:
        ticket_id = self.create_ticket(2)
        client, csrf = self.session_for(2)
        upload = client.post(
            f"/chamados/{ticket_id}/anexos",
            data={
                "csrf_token": csrf,
                "attachment": (BytesIO(b"Teste\n"), "Meu log.txt"),
            },
        )
        self.assertEqual(upload.status_code, 302)
        path = f"/chamados/{ticket_id}/anexos/1"
        download = client.get(path)
        self.assertEqual(download.data, b"Teste\n")
        self.assertIn("attachment", download.headers["Content-Disposition"])
        self.assertIn("Meu_log.txt", download.headers["Content-Disposition"])
        other, _ = self.session_for(3)
        self.assertEqual(other.get(path).status_code, 404)
        self.assertEqual(
            other.post(
                f"/chamados/{ticket_id}/anexos",
                data={"csrf_token": self.token(other), "attachment": (BytesIO(b"a"), "a.txt")},
            ).status_code,
            404,
        )

    def test_chamado_com_anexo_invalido_nao_e_criado(self) -> None:
        client, csrf = self.session_for(2)
        response = client.post(
            "/chamados",
            data={
                **self.payload(csrf),
                "attachment": (BytesIO(b"invalido"), "arquivo.jpg"),
            },
        )
        self.assertIn("conteúdo incompatível".encode(), response.data)
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
