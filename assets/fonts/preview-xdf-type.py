#!/usr/bin/env python3
# SPDX-License-Identifier: OFL-1.1
"""Export actual built font contours, without browser or system-font fallback."""
from __future__ import annotations
from html import escape
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.misc.transform import Transform

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'assets/fonts'


def path_for(font,char,x,y,scale):
    glyphset=font.getGlyphSet();pen=SVGPathPen(glyphset)
    glyphset[font.getBestCmap()[ord(char)]].draw(TransformPen(pen,Transform(scale,0,0,-scale,x,y)))
    return pen.getCommands()


def sheet():
    # Every visible specimen is a real glyph path from the delivered TTF.
    chars=list('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789')
    chars+=list('ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎㅏㅓㅗㅜㅡㅣ가나더러모버서오자차코토푸흐곰문값꽃괜읽')
    cols=8;cellw=160;cellh=155;top=160
    rows=(len(chars)+cols-1)//cols;height=top+rows*cellh+55
    svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="1360" height="{height}" viewBox="0 0 1360 {height}">',
         '<title>XDF original glyph outlines</title>',
         '<desc>Actual Xedotum Bold font contours: English alphabet, numerals, original Hangul Jamo and composed syllables. No live specimen text or system-font fallback.</desc>',
         f'<rect width="1360" height="{height}" fill="#f6f5f1"/>',
         '<g font-family="Segoe UI,Malgun Gothic,sans-serif" fill="#72776b">',
         '<text x="40" y="53" font-size="12" letter-spacing="2">XDF / ORIGINAL LETTER DRAWINGS</text>',
         '<text x="40" y="105" font-size="30" fill="#242820">알파벳부터 한글 자모까지, 같은 곡선.</text>',
         '<text x="1320" y="53" font-size="12" text-anchor="end">XEDOTUM BOLD / 0.200</text>',
         '</g>']
    with TTFont(OUTPUT/'ttf/xedotum_bold.ttf') as font:
        for index,char in enumerate(chars):
            col=index%cols;row=index//cols;x=40+col*cellw;y=top+row*cellh
            glyph=font['glyf'][font.getBestCmap()[ord(char)]]
            glyph.recalcBounds(font['glyf'])
            w=max(glyph.xMax-glyph.xMin,1);h=max(glyph.yMax-glyph.yMin,1)
            scale=min(102/w,90/h)
            dx=x+cellw*.5-w*scale*.5-glyph.xMin*scale
            dy=y+114+glyph.yMin*scale
            label=escape(char)
            svg.append(f'<path d="M{x} {y+cellh-1}h{cellw-12}" fill="none" stroke="#dedfd5"/>')
            svg.append(f'<path d="M{x+12} {y+114}h{cellw-36}" fill="none" stroke="#e5e6dd" stroke-dasharray="3 4"/>')
            svg.append(f'<path d="{path_for(font,char,dx,dy,scale)}" fill="#f04b2d"/>')
            svg.append(f'<text x="{x+12}" y="{y+139}" font-family="Segoe UI,Malgun Gothic,sans-serif" font-size="11" fill="#8a8e7f">{label} / U+{ord(char):04X}</text>')
    svg.append('</svg>')
    (OUTPUT/'original-glyphs.svg').write_text('\n'.join(svg),encoding='utf-8')


def anatomy():
    svg=['<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="470" viewBox="0 0 1440 470">',
         '<title>XDF letter anatomy</title>',
         '<desc>Outlined X, D, F with restrained text proportions and original O, M, Hangul mieum, ieung, rieul glyphs directly extracted from Xedotum Medium. These are separate normal characters, not the XDF logo ligature.</desc>',
         '<rect width="1440" height="470" rx="20" fill="#eeede6"/>',
         '<text x="40" y="43" font-family="Segoe UI,Malgun Gothic,sans-serif" font-size="12" fill="#797b71" letter-spacing="1">XDF → OUR LETTERS / MEDIUM</text>']
    chars=list('XDFOMㅁㅇㄹ')
    with TTFont(OUTPUT/'ttf/xedotum_medium.ttf') as font:
        for i,char in enumerate(chars):
            x=40+(i%4)*350;y=65+(i//4)*185
            glyph=font['glyf'][font.getBestCmap()[ord(char)]];glyph.recalcBounds(font['glyf'])
            w=glyph.xMax-glyph.xMin;h=glyph.yMax-glyph.yMin;scale=min(230/w,116/h)
            dx=x+(310-w*scale)/2-glyph.xMin*scale;dy=y+130+glyph.yMin*scale
            svg.append(f'<path d="{path_for(font,char,dx,dy,scale)}" fill="{("#f04b2d" if i<3 else "#262922")}"/>')
            svg.append(f'<text x="{x+155}" y="{y+162}" text-anchor="middle" font-family="Segoe UI,Malgun Gothic,sans-serif" font-size="12" fill="#858a7c">{char}</text>')
    svg.append('</svg>');(OUTPUT/'letter-anatomy.svg').write_text('\n'.join(svg),encoding='utf-8')


if __name__=='__main__':
    sheet();anatomy();print('Real TTF outlines exported to original-glyphs.svg and letter-anatomy.svg')
