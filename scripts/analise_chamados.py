"""Análise descritiva dos chamados fictícios de um banco migrado."""

import argparse
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from statistics import mean


def analyze(path: Path) -> None:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute("""
            SELECT categoria, status, prioridade, csat, substr(aberto_em, 1, 10)
            FROM vw_relatorio_chamados WHERE demonstracao = 1
        """).fetchall()
    print(f"Chamados fictícios: {len(rows)}")
    for label, index in (("Categorias", 0), ("Status", 1), ("Prioridades", 2), ("Dias UTC", 4)):
        print(f"\n{label}")
        for value, count in Counter(row[index] for row in rows).most_common():
            print(f"  {value}: {count}")
    scores = [row[3] for row in rows if row[3] is not None]
    if scores:
        print(f"\nCSAT médio: {mean(scores):.1f}/5 ({len(scores)} avaliações)")
    else:
        print("\nNenhuma avaliação fictícia registrada.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, nargs="?", default=Path("instance/demonstracao.db"))
    args = parser.parse_args()
    try:
        analyze(args.database)
    except sqlite3.Error as error:
        parser.exit(1, f"Abra ou migre o banco antes da análise. SQLite: {error}\n")


if __name__ == "__main__":
    main()
