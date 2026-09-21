"""Remap TripoSR ckpt (transformers 4.x ViT names) to transformers 5.x names."""
import re


def remap_key(key: str) -> str:
    prefix = "image_tokenizer.model.encoder.layer."
    if key.startswith(prefix):
        rest = key[len(prefix):]
        rest = rest.replace("attention.attention.query.", "attention.q_proj.")
        rest = rest.replace("attention.attention.key.", "attention.k_proj.")
        rest = rest.replace("attention.attention.value.", "attention.v_proj.")
        rest = rest.replace("attention.output.dense.", "attention.o_proj.")
        rest = rest.replace("intermediate.dense.", "mlp.fc1.")
        rest = re.sub(r"(?<!mlp\.)output\.dense\.", "mlp.fc2.", rest)
        return "image_tokenizer.model.layers." + rest
    return key


def remap_state_dict(ckpt: dict) -> dict:
    return {remap_key(k): v for k, v in ckpt.items()}
