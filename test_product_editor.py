import base64
import csv
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import engine
from product_editor import details, save, store_photos

class ProductWorkflow(unittest.TestCase):
    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        self.root = Path(self.resources.enter_context(tempfile.TemporaryDirectory()))
        original = engine.ROOT
        import shutil
        shutil.copytree(original/'templates',self.root/'templates')
        shutil.copy(original/'settings.json',self.root/'settings.json')
        data=self.root/'data';data.mkdir()
        self.resources.enter_context(patch.object(engine,'ROOT',self.root))
        self.resources.enter_context(patch.object(engine,'DATA',data))
        self.photo=base64.b64encode((original/'demo'/'grove-1.jpg').read_bytes()).decode()
        self.product=dict(sku='P01',name='Notebook',category='Books',price='600',cost='190',fees='10',
                          stock='5',product_url='https://prodxmart.com/product/notebook',
                          new=[{'base64':self.photo}],media_ok=True)

    def test_edit_retains_photos_and_rejects_stale_form(self):
        save(self.product)
        p=details('P01')
        self.assertEqual(engine.catalogue('store')['P01']['issues'],[])
        save(dict(self.product,new=[],keep=p['photos'],version=p['version'],stock='1'))
        self.assertIn('Low stock',engine.catalogue('store')['P01']['issues'])
        with self.assertRaisesRegex(ValueError,'latest version'):
            save(dict(self.product,version=p['version']))
        self.assertEqual(details('P01')['photos'],p['photos'])

    def test_only_nonpositive_unit_margins_trigger_low_margin(self):
        # Selling price minus cost leaves ₹31 before fees.
        cases = [('0', []), ('30.99', []), ('31', ['Low margin']), ('31.01', ['Low margin'])]
        for index, (fees, expected_issues) in enumerate(cases):
            with self.subTest(fees=fees):
                sku = f'MARGIN-{index}'
                save(dict(self.product, sku=sku, price='343', cost='312', fees=fees))
                self.assertEqual(engine.catalogue('store')[sku]['issues'], expected_issues)

    def test_disconnected_reader_can_discover_public_gallery(self):
        url = 'https://prodxmart.com/products/maroon'
        page = b'''<meta property="og:image" content="/brand-logo.png">
          <script type="application/ld+json">{"@graph":[
            {"@type":"Product","sku":"OTHER","image":"/other.jpg"},
            {"@type":"Product","sku":"SMP787747","image":[{"url":"/front.jpg"},"/back.jpg"]}
          ]}</script><img alt="Maroon shirt" data-src="/back.jpg">'''
        with patch('product_editor.fetch_public',return_value=(page,url)):
            self.assertEqual(store_photos(url,'SMP787747','Maroon shirt')['images'],
                             ['https://prodxmart.com/front.jpg','https://prodxmart.com/back.jpg'])

    def test_listing_gallery_reads_all_angles_from_exact_slug(self):
        slug = 'grey-khadi-bag-SMP4618'
        url = 'https://prodxmart.com/product/' + slug
        images = ['https://cdn.factori.com/' + s + '.webp' for s in ('front', 'back', 'inside', 'details')]
        row = {'rsp_slug_url':slug, 'rsp_org_product_id':8432, 'rsp_id':1000004618,
               'rsp_multi_images':json.dumps(images), 'rsp_img_path':images[0]}
        payload = {'success':True, 'data':[dict(row, rsp_slug_url='other', rsp_multi_images=['/unrelated.jpg']), row]}
        with patch('product_editor.fetch_public',return_value=(json.dumps(payload).encode(),url)) as fetch:
            got = store_photos(url, 'SKUFACTORI8432', 'Grey Khadi bag')
            fetch.assert_called_once_with('https://prodxmart.com/api/v1/products/product/'+slug+'?domain=prodxmart.com', product_page=True)
        self.assertEqual(got['images'], images)
        self.assertCountEqual(got['identifiers'], ['SKUFACTORI8432','1000004618','SMP4618'])

    def test_unavailable_product_record_falls_back_to_page(self):
        url = 'https://prodxmart.com/product/notebook'
        html = b'<img alt="Notebook" src="/front.jpg">'
        with patch('product_editor.fetch_public', side_effect=[OSError('offline'), (html,url)]):
            self.assertEqual(store_photos(url,'P01','Notebook')['images'], ['https://prodxmart.com/front.jpg'])

    def test_invalid_photo_does_not_create_partial_product(self):
        with self.assertRaisesRegex(ValueError,'photo'):
            save(dict(self.product,new=[{'base64':self.photo},{'base64':'QUJD'}]))
        self.assertEqual(engine.catalogue('store'),{})

    def test_failed_link_identifies_photo_and_can_be_replaced_with_upload(self):
        upload = {'base64': self.photo}
        sources = [upload, {'url': 'https://example.com/a.jpg'}, {'url': 'https://example.com/b.jpg'}]
        with patch('product_editor.public_image', side_effect=[
                base64.b64decode(self.photo), ValueError('The image host returned HTTP 404.')]):
            with self.assertRaisesRegex(ValueError, 'Image link 2: .*404'):
                save(dict(self.product, new=sources))
        self.assertEqual(engine.catalogue('store'), {})
        save(dict(self.product, new=[upload]))
        self.assertEqual(len(details('P01')['photos']), 1)

    def test_draft_without_images_and_private_link(self):
        save(dict(self.product,new=[],media_ok=False))
        self.assertIn('Photo missing',engine.catalogue('store')['P01']['issues'])
        with self.assertRaises(ValueError):
            save(dict(self.product,sku='P02',new=[{'url':'https://127.0.0.1/test'}]))
        self.assertNotIn('P02',engine.catalogue('store'))

    def test_other_product_edit_preserves_verification_time(self):
        save(self.product)
        original=engine.catalogue('store')['P01']['_studio_verified_at']
        save(dict(self.product,sku='P02'))
        self.assertEqual(engine.catalogue('store')['P01']['_studio_verified_at'],original)

    def test_imported_extra_columns_and_custom_headers(self):
        engine.directory('store')
        cfg=engine.config();cfg['catalogue_columns']={'sku':'Item Code'}
        (self.root/'settings.json').write_text(json.dumps(cfg))
        path=self.root/'store/catalogue.csv'
        heads=['Item Code' if h=='sku' else h for h in engine.HEADERS]+['supplier_note']
        with path.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=heads);w.writeheader()
            w.writerow({**{k:self.product.get(k,'') for k in engine.HEADERS if k!='sku'},'Item Code':'IMPORT','supplier_note':'Keep'})
        old=details('IMPORT')
        save(dict(self.product,sku='IMPORT',version=old['version']))
        with path.open(encoding='utf-8-sig') as f:self.assertEqual(next(csv.DictReader(f))['supplier_note'],'Keep')

    def test_photo_order_and_positive_margin_eligibility(self):
        img_a = base64.b64decode(self.photo)
        from PIL import Image
        from io import BytesIO
        buf_b = BytesIO()
        Image.new('RGB', (1200, 1200), (20, 90, 180)).save(buf_b, 'PNG')
        b64_b = base64.b64encode(buf_b.getvalue()).decode()
        save(dict(self.product, sku='ORDER1', price='250', cost='180', fees='35', new=[{'base64': self.photo}]))
        row = details('ORDER1')
        self.assertEqual(len(row['photos']), 1)
        first_photo = row['photos'][0]
        save(dict(
            self.product,
            sku='ORDER1',
            price='250',
            cost='180',
            fees='35',
            version=row['version'],
            keep=[first_photo],
            new=[{'base64': b64_b}],
            photo_order=['new:0', 'keep:' + first_photo],
        ))
        updated = details('ORDER1')
        self.assertEqual(len(updated['photos']), 2)
        self.assertEqual(updated['photos'][1], first_photo)
        self.assertNotEqual(updated['photos'][0], first_photo)
        cat_item = engine.catalogue('store')['ORDER1']
        self.assertEqual(float(cat_item['margin']), 35.0)
        self.assertEqual(cat_item['issues'], [])

if __name__=='__main__':
    unittest.main()
