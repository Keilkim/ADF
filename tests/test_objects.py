"""Object edits preserve overlapping content, repeated resources and PDF vectors."""
import io
from pathlib import Path
import tempfile
import unittest
import pymupdf
from PIL import Image
from adf.document import PdfDocument
from adf.pdf_objects import (delete_object, edit_object, object_layer, operations, page_objects, path_parts,
    reshape_path, scan_page, style_object, transform_object)


class ObjectDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='xdf-objects-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'objects.pdf'
        with pymupdf.open() as doc:
            page = doc.new_page(width=400,height=500)
            page.draw_rect((30,30,110,110),color=(1,0,0),fill=(0,1,0),fill_opacity=.6)
            page.insert_text((40,70),'overlapping text (S Do)')
            page.draw_circle((180,100),25,color=(0,0,1))
            image = io.BytesIO()
            Image.new('RGBA',(30,20),(200,10,20,160)).save(image,format='PNG')
            xref = page.insert_image((40,200,100,240),stream=image.getvalue())
            page.insert_image((160,200,220,240),xref=xref)
            other = doc.new_page(width=400,height=500)
            other.insert_image((40,200,100,240),xref=xref)
            doc.save(self.path)
        self.original = self.path.read_bytes()
        self.document = PdfDocument()
        self.addCleanup(self.document.close)
        self.document.open(self.path)

    def test_move_and_delete_shape_preserve_overlapping_text_and_other_shapes(self):
        document = self.document
        before_text = document.doc[0].get_text()
        document.move_object(0,0,(70,60))
        drawings = document.doc[0].get_drawings()
        self.assertEqual(tuple(drawings[0]['rect']),(100,90,180,170))
        self.assertAlmostEqual(drawings[0]['fill_opacity'],.6,places=5)
        self.assertEqual(tuple(drawings[1]['rect']),(155,75,205,125))
        self.assertEqual(document.doc[0].get_text(),before_text)
        document.remove_object(0,0)
        self.assertEqual(len(document.doc[0].get_drawings()),1)
        self.assertEqual(document.doc[0].get_text(),before_text)
        self.assertEqual(len(document.doc[0].get_image_info()),2)
        self.assertTrue(document.undo())
        self.assertTrue(document.undo())
        self.assertEqual(tuple(document.doc[0].get_drawings()[0]['rect']),(30,30,110,110))
        self.assertTrue(document.redo())
        saved = Path(self.temp.name)/'saved.pdf'
        document.save(saved)
        with pymupdf.open(saved) as doc:
            self.assertEqual(tuple(doc[0].get_drawings()[0]['rect']),(100,90,180,170))
            self.assertEqual(doc[0].get_text(),before_text)
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_repeated_image_invocation_moves_and_deletes_independently(self):
        document = self.document
        document.move_object(0,2,(35,45))
        images = document.doc[0].get_image_info(xrefs=True)
        self.assertEqual(tuple(images[0]['bbox']),(75,245,135,285))
        self.assertEqual(tuple(images[1]['bbox']),(160,200,220,240))
        document.remove_object(0,2)
        self.assertEqual([tuple(i['bbox']) for i in document.doc[0].get_image_info()],[(160,200,220,240)])
        self.assertEqual(len(document.doc[1].get_image_info()),1)
        self.assertTrue(document.undo())
        self.assertEqual(len(document.doc[0].get_image_info()),2)

    def test_nested_shared_forms_edit_only_selected_instance_and_page(self):
        with pymupdf.open() as doc:
            for _ in range(2):
                page = doc.new_page(width=400,height=500)
                page.show_pdf_page(page.rect,self.document.doc,0)
                page.show_pdf_page(pymupdf.Rect(0,250,200,500),self.document.doc,0)
            before_other = doc[1].get_pixmap().samples
            page = doc[0]
            edit_object(page,0,(20,40))
            drawings = page.get_drawings()
            self.assertEqual(tuple(drawings[0]['rect']),(50,70,130,150))
            self.assertEqual(tuple(drawings[2]['rect']),(15,265,55,305))
            edit_object(page,2)
            self.assertEqual(len(page.get_image_info()),3)
            self.assertEqual(doc[1].get_pixmap().samples,before_other)
            self.assertEqual(page.get_text().count('overlapping text'),2)

    def test_compound_path_and_hole_remain_one_object(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=300,height=300)
            shape = page.new_shape()
            shape.draw_rect((20,20,120,120))
            shape.draw_rect((50,50,90,90))
            shape.finish(color=None,fill=(1,0,0),even_odd=True)
            shape.commit()
            self.assertEqual(len(page_objects(page)),1)
            edit_object(page,0,(60,30))
            drawing = page.get_drawings()[0]
            self.assertTrue(drawing['even_odd'])
            self.assertEqual(len(drawing['items']),2)
            edit_object(page,0)
            self.assertEqual(page.get_drawings(),[])

    def test_object_layer_contains_one_object_and_no_overlapping_text(self):
        for index in range(4):
            with self.subTest(object=index), object_layer(self.document.doc[0],index) as layer:
                self.assertEqual(layer[0].get_text(),'')
                self.assertEqual(len(page_objects(layer[0])),1)
        self.assertFalse(self.document.dirty)
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_rotated_cropped_pages_keep_hit_boxes_and_edit_coordinates_consistent(self):
        for rotation in (0,90,180,270):
            with self.subTest(rotation=rotation), pymupdf.open() as doc:
                page = doc.new_page(width=500,height=600)
                page.draw_rect((100,150,180,230),fill=(1,0,0))
                page.set_cropbox(pymupdf.Rect(50,100,450,550))
                page.set_rotation(rotation)
                self.assertTrue(pymupdf.Rect(page_objects(page)[0]['rect']).contains(page.get_drawings()[0]['rect']))
                edit_object(page,0,(20,30))
                self.assertEqual(tuple(page.get_drawings()[0]['rect']),(70,80,150,160))

    def test_paths_before_shared_image_keep_paint_order_after_movement(self):
        page = self.document.doc[0]
        before = [kind for kind,_ in page.get_bboxlog()]
        edit_object(page,0,(20,30))
        self.assertEqual([kind for kind,_ in page.get_bboxlog()],before)

    def test_pdf_strings_and_arrays_do_not_create_fake_operations(self):
        data = b'BT [(hello \\( S Do \\)) 20 <446f>] TJ ET % Do S\n 10 10 20 20 re f'
        self.assertEqual([o.name for o in operations(data)],[b'BT',b'TJ',b'ET',b're',b'f'])

    def test_failed_move_rolls_back_without_history_changes(self):
        self.assertRaises(ValueError,self.document.move_object,0,0,(float('nan'),10))
        self.assertFalse(self.document.dirty)
        self.assertFalse(self.document.can_undo)
        self.assertEqual(tuple(self.document.doc[0].get_drawings()[0]['rect']),(30,30,110,110))

    def test_shape_style_preserves_geometry_opacity_text_and_supports_undo_save(self):
        document = self.document
        before = document.doc[0].get_drawings()
        text = document.doc[0].get_text()
        document.style_object(0,0,fill=(.2,.4,.8),stroke=None,width=3.)
        drawing, other = document.doc[0].get_drawings()
        self.assertEqual(tuple(drawing['rect']),tuple(before[0]['rect']))
        self.assertEqual(drawing['type'],'f')
        self.assertIsNone(drawing['color'])
        for actual, expected in zip(drawing['fill'],(.2,.4,.8)):
            self.assertAlmostEqual(actual,expected,places=6)
        self.assertAlmostEqual(drawing['fill_opacity'],.6,places=5)
        self.assertEqual({k:v for k,v in other.items() if k != 'seqno'},
                         {k:v for k,v in before[1].items() if k != 'seqno'})
        self.assertEqual(document.doc[0].get_text(),text)
        self.assertTrue(document.undo())
        self.assertEqual(document.doc[0].get_drawings(),before)
        self.assertTrue(document.redo())
        document.style_object(0,0,fill=None,stroke=(.6,.2,.4),width=4.)
        drawing = document.doc[0].get_drawings()[0]
        self.assertEqual(drawing['type'],'s')
        self.assertAlmostEqual(drawing['width'],4.)
        saved = Path(self.temp.name)/'styled.pdf'
        document.save(saved)
        with pymupdf.open(saved) as reopened:
            self.assertEqual(reopened[0].get_drawings(),document.doc[0].get_drawings())
            self.assertEqual(reopened[0].get_text(),text)
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_style_isolates_shared_form_and_uses_page_point_stroke_width(self):
        with pymupdf.open() as doc:
            for _ in range(2):
                page = doc.new_page(width=400,height=500)
                page.show_pdf_page(page.rect,self.document.doc,0)
                page.show_pdf_page(pymupdf.Rect(0,250,200,500),self.document.doc,0)
            unchanged = doc[1].get_pixmap().samples
            before = doc[0].get_drawings()
            # This is the smaller of two invocations of the same Form resource.
            style_object(doc[0],4,stroke=(.1,.2,.3),fill=None,width=6.)
            after = doc[0].get_drawings()
            self.assertEqual(after[:2],before[:2])
            self.assertEqual({k:v for k,v in after[3].items() if k != 'seqno'},
                             {k:v for k,v in before[3].items() if k != 'seqno'})
            self.assertEqual(after[2]['type'],'s')
            self.assertAlmostEqual(after[2]['width'],6.)
            self.assertAlmostEqual(page_objects(doc[0])[4]['width'],6.)
            self.assertEqual(doc[1].get_pixmap().samples,unchanged)

    def test_style_preserves_even_odd_hole_and_surrounding_graphics_state(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=300,height=300)
            xref = doc.get_new_xref()
            doc.update_object(xref,'<<>>')
            doc.update_stream(xref,b'.2 .4 .6 rg 2 w 10 10 80 80 re 30 30 20 20 re f* '
                                  b'100 100 30 30 re f q .8 g 3 w 150 150 30 30 re B Q '
                                  b'200 200 30 30 re B')
            page.set_contents(xref)
            before = page.get_drawings()
            style_object(page,0,stroke=(.7,.1,.2),width=5.)
            after = page.get_drawings()
            self.assertTrue(after[0]['even_odd'])
            self.assertEqual(after[0]['fill'],before[0]['fill'])
            self.assertEqual(len(after[0]['items']),2)
            # Adding a stroke adds a paint sequence number; other properties stay.
            for actual,original in zip(after[1:],before[1:]):
                self.assertEqual({k:v for k,v in actual.items() if k != 'seqno'},
                                 {k:v for k,v in original.items() if k != 'seqno'})
            targets = page_objects(page)
            self.assertEqual(targets[2]['fill'],(.8,.8,.8))
            self.assertEqual(targets[2]['width'],3.)
            self.assertEqual(targets[3]['fill'],(.2,.4,.6))
            self.assertEqual(targets[3]['width'],2.)

    def test_style_reads_form_inherited_colors_and_width(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=300,height=300)
            form = doc.get_new_xref()
            doc.update_object(form,'<< /Type /XObject /Subtype /Form /BBox [0 0 100 100] >>')
            doc.update_stream(form,b'10 10 30 30 re B')
            doc.xref_set_key(page.xref,'Resources',f'<< /XObject << /F {form} 0 R >> >>')
            stream = doc.get_new_xref()
            doc.update_object(stream,'<<>>')
            doc.update_stream(stream,b'.1 .2 .3 rg .4 G 5 w /F Do')
            page.set_contents(stream)
            target = page_objects(page)[0]
            self.assertEqual(target['fill'],(.1,.2,.3))
            self.assertEqual(target['stroke'],(.4,.4,.4))
            self.assertEqual(target['width'],5.)
            style_object(page,0,fill=None)
            drawing = page.get_drawings()[0]
            self.assertEqual(drawing['type'],'s')
            self.assertAlmostEqual(drawing['width'],5.)

    def test_invalid_style_or_invisible_shape_rolls_back(self):
        before = self.document.doc[0].get_drawings()
        for changes in (dict(fill=None,stroke=None),dict(width=float('nan')),
                        dict(fill=(2,0,0)),dict(stroke=(0,1)),dict(width=-2)):
            with self.subTest(changes=changes):
                self.assertRaises(ValueError,self.document.style_object,0,0,**changes)
                self.assertFalse(self.document.dirty)
                self.assertFalse(self.document.can_undo)
                self.assertEqual(self.document.doc[0].get_drawings(),before)


class ClippingAndTransformTests(unittest.TestCase):
    """Clipping groups move as one, resizing keeps strokes, and layers keep paint order."""

    def page_with_mask(self, doc):
        page = doc.new_page(width=400, height=500)
        image = io.BytesIO()
        Image.new('RGB', (30, 20), (200, 10, 20)).save(image, format='PNG')
        page.insert_image((0, 0, 1, 1), stream=image.getvalue())
        name = page.get_images()[0][7]
        # An image larger than its mask, a frame along the mask, text and a shape after it.
        doc.update_stream(page.get_contents()[0], (
            f'q 0 0 400 500 re W n q 50 300 100 100 re W n q 200 0 0 150 20 280 cm /{name} Do Q '
            f'0 0 1 RG 2 w 50 300 100 100 re S Q 0 1 0 rg 300 300 40 40 re f Q').encode())
        page.insert_text((70, 150), 'front text')
        return page

    def test_mask_image_and_frame_move_together_and_delete_as_one(self):
        with pymupdf.open() as doc:
            page = self.page_with_mask(doc)
            objects, groups = scan_page(page)
            masks = [g for g in groups if g['mask']]
            # The full-page artboard clip is not a mask; the 100 pt square is.
            self.assertEqual(len(masks), 1)
            self.assertEqual(masks[0]['members'], [0, 1])
            self.assertEqual([o['group'] for o in objects], [masks[0]['id']]*2+[None])
            self.assertEqual(tuple(masks[0]['rect']), (50, 100, 150, 200))
            transform_object(page, ('group', masks[0]['id']), (120, 40))
            self.assertEqual(tuple(page.get_image_info()[0]['bbox']), (140, 110, 340, 260))
            pixels = page.get_pixmap()
            # The image still shows inside the moved mask and stays cut outside it.
            self.assertEqual(pixels.pixel(220, 190), (200, 10, 20))
            self.assertEqual(pixels.pixel(300, 120), (255, 255, 255))
            self.assertEqual(tuple(page.get_drawings()[0]['rect']), (170, 140, 270, 240))
            self.assertEqual(tuple(page.get_drawings()[1]['rect']), (300, 160, 340, 200))
            self.assertIn('front text', page.get_text())
            delete_object(page, ('group', masks[0]['id']))
            self.assertEqual(page.get_image_info(), [])
            self.assertEqual(len(page.get_drawings()), 1)
            self.assertIn('front text', page.get_text())

    def test_layers_split_paint_order_around_the_selection(self):
        with pymupdf.open() as doc:
            page = self.page_with_mask(doc)
            group = ('group', next(g['id'] for g in scan_page(page)[1] if g['mask']))
            with object_layer(page, group, 'below') as below:
                self.assertEqual(page_objects(below[0]), [])
            with object_layer(page, group, 'object') as layer:
                self.assertEqual([o['kind'] for o in page_objects(layer[0])], ['image', 'path'])
                self.assertEqual(layer[0].get_text(), '')
            with object_layer(page, group, 'above') as above:
                self.assertEqual([o['kind'] for o in page_objects(above[0])], ['path'])
                self.assertIn('front text', above[0].get_text())
            with object_layer(page, 2, 'below') as below:
                self.assertEqual(len(page_objects(below[0])), 2)
                self.assertEqual(below[0].get_text(), '')

    def test_resize_keeps_stroke_width_on_plain_and_rotated_pages(self):
        for rotation in (0, 90):
            with self.subTest(rotation=rotation), pymupdf.open() as doc:
                page = doc.new_page(width=300, height=300)
                page.draw_rect((50, 60, 90, 80), color=(0, 0, 0), width=3)
                page.set_rotation(rotation)
                # Twice as wide and three times as tall, anchored at the top left (50, 60).
                transform_object(page, 0, pymupdf.Matrix(2, 0, 0, 3, -50, -120))
                drawing = page.get_drawings()[0]
                self.assertEqual(tuple(drawing['rect']), (50, 60, 130, 120))
                self.assertAlmostEqual(drawing['width'], 3.)

    def test_nodes_move_anchors_and_curve_handles(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=300, height=300)
            page.draw_circle((100, 100), 40, color=(0, 0, 0))
            page.draw_rect((150, 150, 200, 200), color=(0, 0, 0))
            circle = page_objects(page)[0]
            parts = path_parts(page, circle)
            self.assertEqual({name for name, _ in parts} - {'h'}, {'m', 'c'})
            top = min((i for i, (name, _) in enumerate(parts) if name == 'c'), key=lambda i: parts[i][1][2].y)
            parts[top] = ('c', [parts[top][1][0], parts[top][1][1], parts[top][1][2] + (0, -30)])
            reshape_path(page, 0, parts)
            self.assertAlmostEqual(page.get_drawings()[0]['rect'].y0, 30, delta=.5)
            square = path_parts(page, page_objects(page)[1])
            self.assertEqual([name for name, _ in square][:5], ['m', 'l', 'l', 'l', 'h'])
            corner = max(range(4), key=lambda i: square[i][1][0].x+square[i][1][0].y)
            square[corner] = (square[corner][0], [square[corner][1][0] + (25, 25)])
            reshape_path(page, 1, square)
            self.assertEqual(tuple(page.get_drawings()[1]['rect']), (150, 150, 225, 225))
            self.assertRaises(ValueError, reshape_path, page, 1, square[:-1])
