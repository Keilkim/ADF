#!/usr/bin/env python3
# SPDX-License-Identifier: OFL-1.1
"""Build XDF's original bilingual outline families (version 0.200).

The Latin alphabet and six contextual Hangul layouts are constructed in
xdf_type_geometry.py. No Noto, system-font, or other typeface outlines are used.
"""
from __future__ import annotations
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
from types import SimpleNamespace
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from xdf_type_geometry import Design,component_shapes,to_glyph,INITIALS,VOWELS,FINALS,VERTICAL,HORIZONTAL,FAMILY_RULES
from fontTools.fontBuilder import FontBuilder
from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.misc.transform import Transform
from fontTools.otlLib.builder import buildLigatureSubstSubtable,buildLookup
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.reverseContourPen import ReverseContourPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.svgLib.path import parse_path
from fontTools.ttLib import newTable,woff2
from fontTools.ttLib.tables import otTables

OUTPUT=ROOT/'assets/fonts'
STYLES=[('Light',300),('Medium',500),('Bold',700),('ExtraBold',800)]
FAMILIES={'xebatang':'Xebatang','xedotum':'Xedotum'}


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def logo_glyph():
    cap=760; scale=cap/478
    source=ET.parse(ROOT/'assets/xdf-wordmark.svg')
    pen=TTGlyphPen(None)
    curve=Cu2QuPen(pen,max_err=.4,reverse_direction=True)
    target=TransformPen(curve,Transform(scale,0,0,-scale,20,cap))
    for identity in ('xdf-x','xdf-d','xdf-f'):
        element=next(e for e in source.iter() if e.get('id')==identity)
        for index,path in enumerate(filter(None,(s.strip() for s in re.split(r'(?=[Mm])',element.get('d'))))):
            parse_path(path,ReverseContourPen(target) if identity=='xdf-d' and index==1 else target)
    return pen.glyph(),round(1871*scale+40)


def add_layout(font,cmap):
    first={}; last={}
    for lead in range(19):
        for vowel in range(21):
            syllable=0xAC00+(lead*21+vowel)*28
            first[(cmap[0x1100+lead],cmap[0x1161+vowel])]=cmap[syllable]
            for final in range(1,28):
                last[(cmap[syllable],cmap[0x11A7+final])]=cmap[syllable+final]
    logo={(cmap[88],cmap[68],cmap[70]):'XDF.brand'}
    table=otTables.GSUB();table.Version=0x00010000
    table.LookupList=otTables.LookupList()
    table.LookupList.Lookup=[buildLookup([buildLigatureSubstSubtable(mapping)],table='GSUB') for mapping in (first,last,logo)]
    table.LookupList.LookupCount=3
    table.FeatureList=otTables.FeatureList();table.FeatureList.FeatureRecord=[]
    for tag,indexes in [('ccmp',[0,1]),('dlig',[2])]:
        record=otTables.FeatureRecord();record.FeatureTag=tag
        record.Feature=otTables.Feature();record.Feature.FeatureParams=None
        record.Feature.LookupListIndex=indexes;record.Feature.LookupCount=len(indexes)
        table.FeatureList.FeatureRecord.append(record)
    table.FeatureList.FeatureCount=2
    table.ScriptList=otTables.ScriptList();table.ScriptList.ScriptRecord=[]
    for script_tag in ('DFLT','hang','latn'):
        record=otTables.ScriptRecord();record.ScriptTag=script_tag;record.Script=otTables.Script()
        language=otTables.LangSys();language.LookupOrder=None;language.ReqFeatureIndex=65535
        language.FeatureIndex=[0,1];language.FeatureCount=2
        record.Script.DefaultLangSys=language;record.Script.LangSysRecord=[];record.Script.LangSysCount=0
        table.ScriptList.ScriptRecord.append(record)
    table.ScriptList.ScriptCount=3
    gsub=newTable('GSUB');gsub.table=table;font['GSUB']=gsub


def web_export(font,path):
    original=woff2.brotli
    woff2.brotli=SimpleNamespace(MODE_FONT=original.MODE_FONT,decompress=original.decompress,
        compress=lambda data,**options:original.compress(data,quality=8,**options))
    try:
        font.flavor='woff2';font.save(path)
    finally:
        font.flavor=None;woff2.brotli=original


def build_one(key,style,weight):
    print(f'Draw original {FAMILIES[key]} {style}',flush=True)
    design=Design(key,weight)
    glyphs={'.notdef':to_glyph(design.bowl(650,760))};metrics={'.notdef':(760,0)};cmap={}
    for cp in range(0x20,0x7F):
        name=f'uni{cp:04X}';shape,advance=design.latin(chr(cp))
        glyphs[name]=to_glyph(shape);metrics[name]=(round(advance),round(shape.bounds[0]) if not shape.is_empty else 0);cmap[cp]=name
    for cp in [0xA0,0xA1,0xA3,0xA5,0xB7,0xBF,0xD7,0x2013,0x2014,0x2018,0x2019,0x201C,0x201D,0x2026,0x20A9,0x20AC]:
        name=f'uni{cp:04X}';character=' ' if cp==0xA0 else chr(cp)
        shape,advance=design.latin(character);glyphs[name]=to_glyph(shape);metrics[name]=(round(advance),round(shape.bounds[0]) if not shape.is_empty else 0);cmap[cp]=name
    components,layouts=component_shapes(design)
    for name,shape in components.items():
        glyphs[name]=to_glyph(shape);metrics[name]=(1000,round(shape.bounds[0]))
    for lead in range(19):
        for vowel,j in enumerate(VOWELS):
            form='v' if j in VERTICAL else 'h' if j in HORIZONTAL else 'm'
            for final in range(28):
                cp=0xAC00+(lead*21+vowel)*28+final;name=f'uni{cp:04X}'
                suffix=layouts[(form,bool(final))]
                pen=TTGlyphPen(glyphs)
                pen.addComponent(f'cho.{suffix}.{lead}',(1,0,0,1,0,0))
                pen.addComponent(f'jung.{suffix}.{vowel}',(1,0,0,1,0,0))
                if final: pen.addComponent(f'jong.{final}',(1,0,0,1,0,0))
                glyphs[name]=pen.glyph();metrics[name]=(1000,0);cmap[cp]=name
    # Standalone compatibility Jamo and modern conjoining Jamo are original too.
    consonants=list(dict.fromkeys(INITIALS+FINALS[1:]))
    for j in consonants:
        cp=ord(j);name=f'uni{cp:04X}'
        shape=design.consonant(j,810,820)
        glyphs[name]=to_glyph(shape);metrics[name]=(1000,0);cmap[cp]=name
    for index,j in enumerate(INITIALS): cmap[0x1100+index]=cmap[ord(j)]
    for index,j in enumerate(FINALS[1:],1): cmap[0x11A7+index]=cmap[ord(j)]
    for index,j in enumerate(VOWELS):
        cp=ord(j);name=f'uni{cp:04X}'
        shape=design.vowel(j,810,820) if j in VERTICAL or j in HORIZONTAL else design.combined_vowel(j,False)
        glyphs[name]=to_glyph(shape);metrics[name]=(1000,0);cmap[cp]=name;cmap[0x1161+index]=name
    glyphs['XDF.brand'],advance=logo_glyph();metrics['XDF.brand']=(advance,20)
    builder=FontBuilder(1000,isTTF=True)
    builder.setupGlyphOrder(list(glyphs))
    builder.setupCharacterMap(cmap)
    builder.setupGlyf(glyphs)
    # LSB follows the actual component ink bounds, including the narrow vowels.
    for name,glyph in glyphs.items():
        glyph.recalcBounds(builder.font['glyf'])
        metrics[name]=(metrics[name][0],getattr(glyph,'xMin',0))
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=1000,descent=-300,lineGap=0)
    family=FAMILIES[key];ps=f'{family}-{style}'
    builder.setupNameTable(dict(familyName=family if style=='Bold' else family+' '+style,
        styleName='Bold' if style=='Bold' else 'Regular',uniqueFontIdentifier=f'XDF:0.200:{ps}',
        fullName=family+' '+style,psName=ps,version='Version 0.200',
        typographicFamily=family,typographicSubfamily=style,
        copyright='Copyright 2026 XDF contributors. Original Latin and Hangul outlines.',
        description='Original XDF modular type: broad proportions, sculpted joins, rounded counters.',
        licenseDescription='This Font Software is licensed under the SIL Open Font License, Version 1.1.',
        licenseInfoURL='https://openfontlicense.org/'))
    builder.setupOS2(version=4,usWeightClass=weight,usWidthClass=5,fsType=0,
        fsSelection=(1<<7)|((1<<5) if weight>=700 else (1<<6)),
        sTypoAscender=1000,sTypoDescender=-300,sTypoLineGap=0,usWinAscent=1050,usWinDescent=350,
        sxHeight=550,sCapHeight=760,achVendID='XDF ')
    builder.setupPost(keepGlyphNames=True)
    builder.setupMaxp()
    font=builder.font
    font['head'].fontRevision=.2;font['head'].macStyle=1 if weight>=700 else 0
    font['head'].created=font['head'].modified=3873657600;font.recalcTimestamp=False
    gasp=newTable('gasp');gasp.gaspRange={65535:15};font['gasp']=gasp
    pairs=['AV','AW','AY','VA','WA','YA','TA','To','Te','Ty','Yo','Wo','FA','LT','LY']
    rules='\n'.join(f'pos {cmap[ord(a)]} {cmap[ord(b)]} -45;' for a,b in pairs)
    addOpenTypeFeaturesFromString(font,'languagesystem DFLT dflt; languagesystem latn dflt; feature kern {'+rules+'} kern;')
    add_layout(font,cmap)
    stem=f'{key}_{style.lower()}';ttf=OUTPUT/'ttf'/f'{stem}.ttf';web=OUTPUT/'woff2'/f'{stem}.woff2'
    font.save(ttf);web_export(font,web)
    item=dict(family=family,style=style,weight=weight,ttf=ttf.relative_to(OUTPUT).as_posix(),woff2=web.relative_to(OUTPUT).as_posix(),
        ttf_sha256=digest(ttf),woff2_sha256=digest(web),ttf_bytes=ttf.stat().st_size,woff2_bytes=web.stat().st_size,
        unicode_count=len(cmap),hangul_syllables=11172,glyph_count=len(glyphs),
        outline_origin='original-XDF-construction',latin_original=True,hangul_original=True,
        contextual_hangul_layouts=6,component_count=len(components),stroke=design.stroke,
        logo_feature='dlig',logo_glyph='XDF.brand',logo_glyph_id=font.getGlyphID('XDF.brand'))
    font.close();print(f'Done {family} {style}: original Latin + 11,172 Hangul, {ttf.stat().st_size} bytes',flush=True)
    return item


def package(items):
    css=['/* Original XDF Type 0.200. SIL OFL 1.1. */']
    for f in items:
        css.append("@font-face {font-family:'%s';font-style:normal;font-weight:%s;font-display:swap;src:url('./%s') format('woff2');}"%(f['family'],f['weight'],f['woff2']))
    (OUTPUT/'fonts.css').write_text('\n'.join(css)+'\n',encoding='utf-8')
    license_source=ROOT/'scripts/OFL-1.1.txt'
    (OUTPUT/'OFL.txt').write_text('Copyright 2026 XDF contributors.\nOriginal XDF Latin and Hangul outlines.\n\n'+license_source.read_text(encoding='utf-8'),encoding='utf-8')
    manifest=dict(version='0.200',date='2026-10-01',license='OFL-1.1',origin='Original XDF Latin, numbers, punctuation and contextual Hangul components; no upstream font outlines.',
        upstream_typefaces=[],replaces='Rejected 0.100 Noto derivatives; archived separately, not included in this release.',
        design_rules={'units_per_em':1000,'cap_height':760,'x_height':550,'hangul_advance':1000,
                      'latin_sidebearing':28,'contextual_hangul_layouts':6,'families':FAMILY_RULES},
        build_source='../../scripts/build-xdf-type.py',geometry_source='../../scripts/xdf_type_geometry.py',
        geometry_sha256=digest(ROOT/'scripts/xdf_type_geometry.py'),
        logo_source='../xdf-wordmark.svg',fonts=items)
    (OUTPUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    for src,dest in [(Path(__file__),'build-xdf-type.py'),(ROOT/'scripts/xdf_type_geometry.py','xdf_type_geometry.py'),
                     (ROOT/'scripts/preview-xdf-type.py','preview-xdf-type.py'),(ROOT/'scripts/OFL-1.1.txt','OFL-1.1.txt'),
                     (ROOT/'scripts/requirements-brand-fonts.txt','requirements-build.txt'),(ROOT/'assets/xdf-wordmark.svg','xdf-wordmark.svg'),
                     (ROOT/'assets/xdf-mark.svg','xdf-mark.svg')]:
        shutil.copyfile(src,OUTPUT/dest)
    names=['OFL.txt','OFL-1.1.txt','fonts.css','manifest.json','build-xdf-type.py','xdf_type_geometry.py','preview-xdf-type.py','requirements-build.txt','xdf-wordmark.svg','xdf-mark.svg']
    for optional in ['README.txt','index.html','preview-cover.png','preview.png','preview-document.png','preview-document.pdf','original-glyphs.svg','letter-anatomy.svg']:
        if (OUTPUT/optional).exists(): names.append(optional)
    with zipfile.ZipFile(OUTPUT/'xdf-fonts-0.200.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for name in names: archive.write(OUTPUT/name,name)
        for item in items:
            for kind in ('ttf','woff2'): archive.write(OUTPUT/item[kind],item[kind])


def main():
    for d in ['ttf','woff2']: (OUTPUT/d).mkdir(parents=True,exist_ok=True)
    with ProcessPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(build_one,key,style,weight) for key in FAMILIES for style,weight in STYLES]
        items=[f.result() for f in futures]
    package(items)
    print('Original XDF 0.200: eight bilingual faces exported',flush=True)


if __name__=='__main__': main()
