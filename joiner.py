import io
import os
from PIL import Image
from PyPDF2 import PdfReader, PdfWriter, PdfMerger, PageObject
from PyPDF2.errors import PdfReadError
from reportlab.lib.pagesizes import A4

_OCR_WARNING_EMITTED = False

def converter_imagens_para_pdf(diretorio: str) -> None:
    extensoes_suportadas = ('.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp')
    sucessos = 0

    print(f"Buscando imagens em: {diretorio}\n" + "-" * 30)

    try:
        arquivos = os.listdir(diretorio)
    except OSError as e:
        print(f"Erro ao acessar o diretorio: {e}")
        return

    for arquivo in arquivos:
        if arquivo.lower().endswith(extensoes_suportadas):
            try:
                caminho_imagem = os.path.join(diretorio, arquivo)
                nome_base = os.path.splitext(arquivo)[0]
                nome_pdf = os.path.join(diretorio, f"{nome_base}.pdf")

                if os.path.exists(nome_pdf):
                    print(f"Aviso: '{nome_pdf}' ja existe. Ignorando conversao de '{arquivo}'.")
                    continue

                with Image.open(caminho_imagem) as img:
                    img_rgb = img.convert('RGB')
                    img_rgb.save(nome_pdf, "PDF", resolution=100.0)

                print(f"Convertido: {arquivo} -> {nome_base}.pdf")
                sucessos += 1

            except Exception as e:
                print(f"Erro ao converter {arquivo}: {e}")

    print("-" * 30)
    print(f"Conversao finalizada! {sucessos} imagens convertidas para PDF.\n")


def scale_and_center_page(page: PageObject, target_w: float, target_h: float) -> PageObject:
    rotation = page.get('/Rotate', 0)
    if hasattr(rotation, 'get_object'):
        rotation = rotation.get_object()
    rotation = int(rotation) % 360

    raw_w = float(page.cropbox.width)
    raw_h = float(page.cropbox.height)

    if rotation in [90, 270]:
        vis_w, vis_h = raw_h, raw_w
        target_raw_w, target_raw_h = target_h, target_w
    else:
        vis_w, vis_h = raw_w, raw_h
        target_raw_w, target_raw_h = target_w, target_h

    if vis_w == 0 or vis_h == 0:
        return page

    scale = min(target_w / vis_w, target_h / vis_h)
    page.scale_by(scale)

    new_llx = float(page.cropbox.left)
    new_lly = float(page.cropbox.bottom)
    new_urx = float(page.cropbox.right)
    new_ury = float(page.cropbox.top)

    new_raw_w = new_urx - new_llx
    new_raw_h = new_ury - new_lly

    diff_x = (target_raw_w - new_raw_w) / 2.0
    diff_y = (target_raw_h - new_raw_h) / 2.0

    lower_left = (new_llx - diff_x, new_lly - diff_y)
    upper_right = (new_urx + diff_x, new_ury + diff_y)

    page.mediabox.lower_left = lower_left
    page.mediabox.upper_right = upper_right

    page.cropbox.lower_left = lower_left
    page.cropbox.upper_right = upper_right

    page.trimbox.lower_left = lower_left
    page.trimbox.upper_right = upper_right
    page.bleedbox.lower_left = lower_left
    page.bleedbox.upper_right = upper_right

    return page


def detect_text_based_rotation(page: PageObject, min_chars: int = 20) -> int:
ns = (0, 90, 180, 270)
    scores = {}

    for orientation in orientations:
        try:
            text = page.extract_text(orientations=(orientation,)) or ""
        except Exception:
            text = ""

        scores[orientation] = sum(1 for ch in text if ch.isalnum())

    best_orientation = max(scores, key=scores.get)
    best_score = scores[best_orientation]

    if best_score < min_chars:
        return 0

    return (360 - best_orientation) % 360


def detect_portrait_fallback_rotation(page: PageObject) -> int:
    rotation = page.get('/Rotate', 0)
    if hasattr(rotation, 'get_object'):
        rotation = rotation.get_object()
    rotation = int(rotation) % 360

    w = float(page.cropbox.width)
    h = float(page.cropbox.height)
    if rotation in (90, 270):
        w, h = h, w

    return 90 if w > h else 0


def detect_ocr_based_rotation(file_path: str, page_index: int, min_chars: int = 20) -> int:
    global _OCR_WARNING_EMITTED

    try:
        import importlib
        pytesseract = importlib.import_module("pytesseract")
        pypdfium2 = importlib.import_module("pypdfium2")
    except Exception:
        if not _OCR_WARNING_EMITTED:
            print("Aviso: OCR indisponivel (instale pypdfium2 + pytesseract e o executavel Tesseract para detectar rotacao em scans).")
            _OCR_WARNING_EMITTED = True
        return 0

    try:
        pdf = pypdfium2.PdfDocument(file_path)
        rendered = pdf[page_index].render(scale=1.6)
        pil_image = rendered.to_pil().convert("RGB")
    except Exception:
        return 0

    scores = {}
    for orientation in (0, 90, 180, 270):
        try:
            if orientation == 0:
                probe = pil_image
            else:
                probe = pil_image.rotate(-orientation, expand=True)
            text = pytesseract.image_to_string(probe) or ""
            scores[orientation] = sum(1 for ch in text if ch.isalnum())
        except Exception:
            scores[orientation] = 0

    best_orientation = max(scores, key=scores.get)
    if scores[best_orientation] < min_chars:
        return 0

    return (360 - best_orientation) % 360


def auto_orient_page(page: PageObject) -> PageObject:
    try:
        page.transfer_rotation_to_content()
    except Exception:
        pass

    detected_rotation = detect_text_based_rotation(page)
    if detected_rotation == 0:
        detected_rotation = detect_portrait_fallback_rotation(page)

    if detected_rotation in (90, 180, 270):
        page.rotate(detected_rotation)
        try:
            page.transfer_rotation_to_content()
        except Exception:
            pass

    return page


def _standardize_pdf_in_isolation(file_path: str, target_w: float, target_h: float) -> io.BytesIO:
    reader = PdfReader(file_path)
    isolated_writer = PdfWriter()

    for page_index, page in enumerate(reader.pages):
        page = auto_orient_page(page)

        if detect_text_based_rotation(page) == 0 and detect_portrait_fallback_rotation(page) == 0:
            ocr_rotation = detect_ocr_based_rotation(file_path, page_index)
            if ocr_rotation in (90, 180, 270):
                page.rotate(ocr_rotation)
                try:
                    page.transfer_rotation_to_content()
                except Exception:
                    pass

        standardized = scale_and_center_page(page, target_w, target_h)
        isolated_writer.add_page(standardized)

    buffer = io.BytesIO()
    isolated_writer.write(buffer)
    buffer.seek(0)
    return buffer


def standardize_and_merge(input_dir: str, output_name: str) -> None:
    target_w, target_h = A4
    merger = PdfMerger()

    generated_prefix = "0_standardized_joined"

    try:
        arquivos = sorted(
            [
                f
                for f in os.listdir(input_dir)
                if f.lower().endswith('.pdf')
                and f.lower() != output_name.lower()
                and not f.lower().startswith(generated_prefix)
            ]
        )
    except OSError as e:
        print(f"Erro ao acessar o diretorio para juncao: {e}")
        return

    if not arquivos:
        print("Nenhum arquivo PDF encontrado para unir.")
        return

    print("Iniciando a padronizacao e uniao dos PDFs...")
    in_memory_parts = []

    for pdf_nome in arquivos:
        print(f"Processando: {pdf_nome}...")
        file_path = os.path.join(input_dir, pdf_nome)

        try:
            standardized_part = _standardize_pdf_in_isolation(file_path, target_w, target_h)
            in_memory_parts.append(standardized_part)
            merger.append(standardized_part)
        except (PdfReadError, OSError) as e:
            print(f"Erro ao processar {pdf_nome}: {e}")
            continue

    output_path = os.path.join(input_dir, output_name)
    try:
        with open(output_path, "wb") as f:
            merger.write(f)
        print(f"\nConcluido! Todas as paginas estao no tamanho A4 no arquivo: {output_name}")
    except IOError as e:
        print(f"Erro ao salvar o arquivo final: {e}")
    finally:
        merger.close()
        for part in in_memory_parts:
            part.close()


if __name__ == "__main__":
    try:
        pasta_atual = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        pasta_atual = os.getcwd()

    nome_saida = "0_standardized_joined.pdf"

    converter_imagens_para_pdf(pasta_atual)

    standardize_and_merge(pasta_atual, nome_saida)
