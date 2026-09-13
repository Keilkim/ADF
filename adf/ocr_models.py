"""Pinned CPU models. Downloads are performed by the build helper only."""
MODELS = [
    dict(name='ch_ppocr_mobile_v2.0_cls_mobile.onnx', role='orientation_runtime_dependency',
         url='https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx',
         sha256='e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c'),
    dict(name='ch_PP-OCRv5_det_server.onnx', role='text_detection',
         url='https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv5/det/ch_PP-OCRv5_det_server.onnx',
         sha256='0f8846b1d4bba223a2a2f9d9b44022fbc22cc019051a602b41a7fda9667e4cad'),
    dict(name='korean_PP-OCRv5_rec_mobile.onnx', role='korean_english_text',
         url='https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv5/rec/korean_PP-OCRv5_rec_mobile.onnx',
         sha256='cd6e2ea50f6943ca7271eb8c56a877a5a90720b7047fe9c41a2e541a25773c9b'),
    dict(name='pp_doc_layoutv3.onnx', role='layout_reading_order',
         url='https://www.modelscope.cn/models/RapidAI/RapidLayout/resolve/v1.2.0/onnx/pp_doc_layout/pp_doc_layoutv3.onnx',
         sha256='250dbad1dfb9e4983fab75e1bf5085cd56ec3f41d5c7d0f8623ec74856e7aa67'),
    dict(name='slanet-plus.onnx', role='table_structure',
         url='https://www.modelscope.cn/models/RapidAI/RapidTable/resolve/v2.0.0/slanet-plus.onnx',
         sha256='d57a942af6a2f57d6a4a0372573c696a2379bf5857c45e2ac69993f3b334514b'),
]

# Meaning-based search: the ONNX export of intfloat/multilingual-e5-small (MIT,
# per its model card) and the SentencePiece vocabulary it was trained with.
SEARCH_REVISION = '614241f622f53c4eeff9890bdc4f31cfecc418b3'
SEARCH_MODELS = [
    dict(name='multilingual-e5-small.onnx', role='text_embedding',
         url=f'https://huggingface.co/intfloat/multilingual-e5-small/resolve/{SEARCH_REVISION}/onnx/model.onnx',
         sha256='ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665'),
    dict(name='sentencepiece.bpe.model', role='tokenizer_vocabulary',
         url=f'https://huggingface.co/intfloat/multilingual-e5-small/resolve/{SEARCH_REVISION}/sentencepiece.bpe.model',
         sha256='cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865'),
]
