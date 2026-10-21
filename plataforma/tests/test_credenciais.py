from nucleo.credenciais import derivar_senha, gerar_credencial, hash_senha, verificar_senha

# gerado pelo mosquitto_passwd do eclipse-mosquitto:2.0.20 (senha "segredo123")
HASH_MOSQUITTO = (
    "$7$101$dp9uR5vw2PFoHsVt$5cTWNermXpdfpZdKMyPs3XFmusZihpaZgGkfEXfSdq07oH1q/2nvmrcnmVp76jWRkAHQ8XsR/yqiNDjJCa04RA=="
)


def test_verifica_hash_gerado_pelo_mosquitto():
    assert verificar_senha("segredo123", HASH_MOSQUITTO)
    assert not verificar_senha("segredo124", HASH_MOSQUITTO)


def test_hash_gerado_aqui_tem_o_formato_do_mosquitto():
    codificado = hash_senha("abc")
    partes = codificado.split("$")
    assert partes[1:3] == ["7", "101"] and len(partes[3]) == 16 and len(partes[4]) == 88
    assert verificar_senha("abc", codificado) and not verificar_senha("abd", codificado)
    assert hash_senha("abc") != codificado  # salt aleatório


def test_hash_ausente_ou_malformado_nunca_confere():
    for ruim in (None, "", "texto", "$6$101$x$y", "$7$abc$x$y"):
        assert not verificar_senha("qualquer", ruim)


def test_derivacao_e_deterministica_e_depende_do_segredo():
    a = derivar_senha("segredo", "est-mateiros-01")
    assert a == derivar_senha("segredo", "est-mateiros-01") and len(a) == 32
    assert a != derivar_senha("segredo", "est-formosa-01")
    assert a != derivar_senha("outro", "est-mateiros-01")


def test_credencial_aleatoria():
    assert gerar_credencial() != gerar_credencial() and len(gerar_credencial()) >= 32
