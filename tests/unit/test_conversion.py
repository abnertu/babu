from babu.harness.conversion import ScriptConverter


def test_only_sentences_with_simplified_only_characters_are_converted():
    converter = ScriptConverter("s2tw")
    traditional = "台灣的范先生了解皇后的計畫。准備好了！"
    assert converter.convert(traditional) == traditional
    assert converter.convert("以后我们再处理。\n" + traditional) == "以後我們再處理。\n" + traditional
