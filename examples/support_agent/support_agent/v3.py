"""v3: v2, and it understands "my parcel arrived damaged". Production showed that v2 did not."""

from .v2 import make_answer

answer = make_answer("damaged|broken|not working|arrived (faulty|defective)")
