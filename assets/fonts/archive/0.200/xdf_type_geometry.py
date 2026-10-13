# SPDX-License-Identifier: OFL-1.1
"""Original XDF letter construction. No upstream typeface outlines are used.

All outlines share broad proportions, rounded rectangular counters, concave
joins and gently shaped terminals. Hangul uses six contextual syllable layouts.
Coordinates point up, with an 820-unit Korean body and a 760-unit Latin cap.
"""
from __future__ import annotations
import math
from functools import lru_cache
from pathlib import Path
import xml.etree.ElementTree as ET
from shapely import affinity
from shapely.geometry import Polygon, LineString, Point, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.pens.basePen import BasePen
from fontTools.svgLib.path import parse_path

WEIGHTS = {300:70, 500:110, 700:165, 800:210}
FAMILY_RULES = {
    'xebatang': {'join_radius':.10,'horizontal_ratio':.78,'bowl_horizontal_ratio':.72,
                'serif_span':.65,'serif_extension':.08,'bar_tip':.08},
    'xedotum': {'join_radius':.16,'horizontal_ratio':1.0,'bowl_horizontal_ratio':.96,
               'serif_span':.50,'serif_extension':0,'bar_tip':.08},
}
INITIALS = ['ㄱ','ㄲ','ㄴ','ㄷ','ㄸ','ㄹ','ㅁ','ㅂ','ㅃ','ㅅ','ㅆ','ㅇ','ㅈ','ㅉ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ']
VOWELS = ['ㅏ','ㅐ','ㅑ','ㅒ','ㅓ','ㅔ','ㅕ','ㅖ','ㅗ','ㅘ','ㅙ','ㅚ','ㅛ','ㅜ','ㅝ','ㅞ','ㅟ','ㅠ','ㅡ','ㅢ','ㅣ']
FINALS = ['', 'ㄱ','ㄲ','ㄳ','ㄴ','ㄵ','ㄶ','ㄷ','ㄹ','ㄺ','ㄻ','ㄼ','ㄽ','ㄾ','ㄿ','ㅀ','ㅁ','ㅂ','ㅄ','ㅅ','ㅆ','ㅇ','ㅈ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ']
VERTICAL = {'ㅏ','ㅐ','ㅑ','ㅒ','ㅓ','ㅔ','ㅕ','ㅖ','ㅣ'}
HORIZONTAL = {'ㅗ','ㅛ','ㅜ','ㅠ','ㅡ'}
DOUBLE = {'ㄲ':'ㄱ','ㄸ':'ㄷ','ㅃ':'ㅂ','ㅆ':'ㅅ','ㅉ':'ㅈ'}
CLUSTERS = {'ㄳ':('ㄱ','ㅅ'),'ㄵ':('ㄴ','ㅈ'),'ㄶ':('ㄴ','ㅎ'),'ㄺ':('ㄹ','ㄱ'),
 'ㄻ':('ㄹ','ㅁ'),'ㄼ':('ㄹ','ㅂ'),'ㄽ':('ㄹ','ㅅ'),'ㄾ':('ㄹ','ㅌ'),
 'ㄿ':('ㄹ','ㅍ'),'ㅀ':('ㄹ','ㅎ'),'ㅄ':('ㅂ','ㅅ')}


def union(*items):
    return unary_union([g for g in items if g is not None and not g.is_empty])


def soft_box(x0,y0,x1,y1,r):
    if x1<=x0 or y1<=y0:
        return Polygon()
    r = max(0, min(r, (x1-x0)/2-.01, (y1-y0)/2-.01))
    if r<.1:
        return box(x0,y0,x1,y1)
    return box(x0+r,y0+r,x1-r,y1-r).buffer(r,quad_segs=8)


def fit(shape, width, height, x=0, y=0):
    if shape.is_empty:
        return shape
    a,b,c,d=shape.bounds
    if c-a<.01 or d-b<.01:
        return shape
    return affinity.translate(affinity.scale(shape,xfact=width/(c-a),yfact=height/(d-b),origin=(a,b)),xoff=x-a,yoff=y-b)


def bezier(points, steps=16):
    p0,p1,p2,p3=points
    return [tuple((1-t)**3*p0[k]+3*(1-t)**2*t*p1[k]+3*(1-t)*t*t*p2[k]+t**3*p3[k]
                  for k in range(2)) for t in [i/steps for i in range(steps+1)]]


class CurveSampler(BasePen):
    """Sample our own existing SVG brand artwork, never a typeface."""
    def __init__(self):
        super().__init__(None)
        self.contours=[]
        self.points=[]

    def _moveTo(self,p): self.points=[p]
    def _lineTo(self,p): self.points.append(p)
    def _curveToOne(self,a,b,c):
        self.points.extend(bezier([self.points[-1],a,b,c],32)[1:])
    def _closePath(self): self.contours.append(self.points);self.points=[]
    def _endPath(self): self._closePath()


@lru_cache(maxsize=3)
def brand_outline(char):
    source=Path(__file__).resolve().parents[1]/'assets/xdf-wordmark.svg'
    root=ET.parse(source)
    node=next(e for e in root.iter() if e.get('id')==f'xdf-{char.lower()}')
    pen=CurveSampler();parse_path(node.get('d'),pen)
    shape=Polygon(pen.contours[0])
    for counter in pen.contours[1:]: shape=shape.difference(Polygon(counter))
    # SVG points down; TrueType points up. Keep the artwork's own proportions.
    shape=affinity.scale(shape,xfact=760/478,yfact=-760/478,origin=(0,0))
    x0,y0,_,_=shape.bounds
    return affinity.translate(shape,xoff=-x0,yoff=-y0)


def soften(shape,r):
    """Round both the inward junction and the outside corner of an outline."""
    if not r or shape.is_empty: return shape
    polygons=[shape] if shape.geom_type=='Polygon' else [p for p in shape.geoms if p.geom_type=='Polygon']
    for polygon in polygons:
        for hole in polygon.interiors:
            a,b,c,d=Polygon(hole).bounds
            r=min(r,(c-a)*.16,(d-b)*.16)
    return shape.buffer(r,quad_segs=8).buffer(-r,quad_segs=8).buffer(-r*.55,quad_segs=8).buffer(r*.55,quad_segs=8)


class Design:
    def __init__(self, family, weight):
        self.family=family
        self.serif=family=='xebatang'
        self.rules=FAMILY_RULES[family]
        self.stroke=WEIGHTS[weight]
        self.radius=self.stroke*self.rules['join_radius']

    def serif_tip(self,t):
        # Bracketed, shallow serifs provide a formal text silhouette. The
        # little inward curve is the brand detail, rather than a bulky slab.
        span=t*self.rules['serif_span'];end=t*self.rules['serif_extension']
        p=[(-t*.45,-t*.50)]
        p+=bezier([p[-1],(-t*.20,-t*.50),(-t*.15,-span),(-t*.02,-span)])[1:]
        p.extend([(end,-span),(end,span),(-t*.02,span)])
        p+=bezier([p[-1],(-t*.15,span),(-t*.20,t*.50),(-t*.45,t*.50)])[1:]
        return soften(Polygon(p),t*.07)

    def stroke_path(self, points, t=None, tips=True):
        t=t or self.stroke
        if len(points)<2:
            return Polygon()
        shape=LineString(points).buffer(t/2,cap_style='flat',join_style='round',quad_segs=8)
        if self.serif and tips:
            for end, neighbor in ((points[0],points[1]),(points[-1],points[-2])):
                angle=math.degrees(math.atan2(end[1]-neighbor[1],end[0]-neighbor[0]))
                tip=self.serif_tip(t)
                tip=affinity.rotate(tip,angle,origin=(0,0))
                shape=union(shape,affinity.translate(tip,xoff=end[0],yoff=end[1]))
        return soften(shape,t*self.rules['join_radius'])

    def hbar(self, x0,x1,y,t=None):
        t=(t or self.stroke)*self.rules['horizontal_ratio']
        # The F arm's flat top and soft, receding lower tip are the bar motif.
        top=y+t/2; bottom=y-t/2
        p=[(x0,top),(x1-t*.12,top)]
        tip=t*self.rules['bar_tip']
        p+=bezier([(x1-t*.12,top),(x1+tip,top),(x1+tip,y+t*.14),(x1+tip,y)])[1:]
        p+=bezier([(x1+tip,y),(x1+t*.04,bottom+t*.08),(x1-t*.12,bottom),(x1-t*.22,bottom)])[1:]
        p.append((x0+t*.02,bottom))
        p+=bezier([(x0+t*.02,bottom),(x0-t*.17,bottom),(x0-t*.18,top),(x0,top)])[1:]
        shape=Polygon(p)
        if self.serif:
            shape=union(shape,affinity.translate(affinity.rotate(self.serif_tip(t*.91),180,origin=(0,0)),xoff=x0,yoff=y))
        return shape

    def vbar(self,x,y0,y1,t=None):
        return self.stroke_path([(x,y0),(x,y1)],t)

    def bowl(self,w,h,t=None,roundness=None):
        t=t or self.stroke
        default=roundness is None
        if default: roundness=.46 if self.serif else .38
        outer=soft_box(0,0,w,h,min(w,h)*roundness)
        tx=t*1.03
        ty=t*self.rules['bowl_horizontal_ratio']
        inner=soft_box(tx,ty,w-tx,h-ty,min(w-2*tx,h-2*ty)*(.39 if self.serif and default else .30))
        return outer.difference(inner)

    def brand_letter(self,char):
        shape=brand_outline(char)
        a,b,c,d=shape.bounds
        # Text proportions are quieter than the wide standalone wordmark.
        width={'X':810,'D':720,'F':610}[char]
        if char=='X' and self.stroke<WEIGHTS[800]:
            w=c-a;h=d-b;t=self.stroke*1.2
            descending=bezier([(w*.12,h-t*.52),(w*.18,h*.72),(w*.68,h*.49),(w*.87,t*.52)],32)
            ascending=bezier([(w*.12,t*.52),(w*.28,h*.41),(w*.74,h*.58),(w*.87,h-t*.52)],32)
            shape=fit(soften(union(self.stroke_path(descending,t,tips=False),self.stroke_path(ascending,t,tips=False)),t*.35),width,h)
            return affinity.translate(shape,xoff=28),width+56
        # The chosen brand curves retain a quieter text proportion. Lighter
        # drawings expand the counters of our own artwork, never a typeface.
        inset=(WEIGHTS[800]-self.stroke)*.5
        if inset: shape=shape.buffer(-inset,quad_segs=12)
        shape=fit(shape,width,d-b)
        return affinity.translate(shape,xoff=28),width+56

    def s(self,w,h,t=None):
        t=t or self.stroke
        p=bezier([(w-t*.6,h-t*.6),(w*.2,h+t*.04),(-w*.02,h*.65),(w*.52,h*.50)])
        p+=bezier([(w*.52,h*.50),(w*1.12,h*.25),(w*.82,-h*.12),(t*.6,t*.6)])[1:]
        return self.stroke_path(p,t)

    def latin(self,char):
        t=self.stroke; h=760; w=670
        if char in 'MW': w=880
        if char=='X': w=1110
        if char=='D': w=950
        if char in 'EF': w=690
        if char in 'BPR': w=710
        if char in 'IJ': w=390 if char=='I' else 590
        if char in 'il': w=270
        if char==' ': return Polygon(),360
        if char in 'XDF': return self.brand_letter(char)
        if char.islower(): return self.lower(char)
        if char.isdigit(): return self.digit(char)
        x=t*.62; right=w-t*.62; bottom=t*.53; top=h-t*.53
        ring=self.bowl(w,h)
        if char=='A':
            p=[(0,0),(t*1.1,0),(w*.5,h-t*.8),(w-t*1.1,0),(w,0),(w*.5+t*.23,h),(w*.5-t*.23,h)]
            shape=soften(union(Polygon(p),self.hbar(w*.24,w*.76,h*.35,t*.84)),t*.20)
            if self.serif:
                shape=union(shape,self.hbar(t*.15,t*.99,t*.25,t*.62),self.hbar(w-t*.99,w-t*.15,t*.25,t*.62))
        elif char=='B':
            shape=union(self.vbar(x,bottom,top),self.bowl(w-t*.38,h*.52),
                        affinity.translate(self.bowl(w-t*.43,h*.50),yoff=h*.50))
        elif char=='C':
            shape=ring.difference(box(w*.58,h*.26,w+20,h*.74))
        elif char=='D':
            # D retains a straighter left stem and a generous right bow.
            outer=union(soft_box(0,0,w*.57,h,t*.56),soft_box(w*.19,0,w,h,h*.43))
            inner=soft_box(t*1.12,t*.94,w-t*.96,h-t*.94,min(100,t*.62))
            shape=outer.difference(inner)
        elif char in 'EF':
            shape=union(self.vbar(x,bottom,top),self.hbar(x,right,top),
                        self.hbar(x,w*.79,h*.52))
            if char=='E': shape=union(shape,self.hbar(x,right,bottom))
        elif char=='G':
            shape=union(ring.difference(box(w*.57,h*.40,w+20,h*.76)),
                        self.hbar(w*.51,right,h*.41),self.vbar(right,h*.20,h*.41))
        elif char=='H': shape=union(self.vbar(x,bottom,top),self.vbar(right,bottom,top),self.hbar(x,right,h*.50))
        elif char=='I': shape=self.vbar(w*.50,bottom,top)
        elif char=='J':
            shape=union(self.vbar(right,h*.32,top),ring.difference(box(-30,h*.40,w+30,h+30)).difference(box(-30,h*.24,w*.36,h*.45)))
        elif char=='K': shape=union(self.vbar(x,bottom,top),self.stroke_path([(right,top),(x,h*.46),(right,bottom)]))
        elif char=='L': shape=union(self.vbar(x,bottom,top),self.hbar(x,right,bottom))
        elif char=='M': shape=self.stroke_path([(x,bottom),(x,top),(w*.5,h*.30),(right,top),(right,bottom)])
        elif char=='N': shape=self.stroke_path([(x,bottom),(x,top),(right,bottom),(right,top)])
        elif char=='O': shape=ring
        elif char in 'PQ':
            if char=='P': shape=union(self.vbar(x,bottom,top),affinity.translate(self.bowl(w,h*.62),yoff=h*.38))
            else: shape=union(ring,self.stroke_path([(w*.56,h*.31),(w*.93,-h*.04)],t*.8))
        elif char=='R': shape=union(self.vbar(x,bottom,top),affinity.translate(self.bowl(w,h*.62),yoff=h*.38),self.stroke_path([(w*.45,h*.46),(right,bottom)]))
        elif char=='S': shape=self.s(w,h)
        elif char=='T': shape=union(self.hbar(x,right,top),self.vbar(w*.5,bottom,top))
        elif char=='U': shape=union(ring.difference(box(t*.99,h*.40,w-t*.99,h+10)),self.vbar(x,h*.43,top),self.vbar(right,h*.43,top))
        elif char=='V': shape=self.stroke_path([(x,top),(w*.50,bottom),(right,top)])
        elif char=='W': shape=self.stroke_path([(x,top),(w*.28,bottom),(w*.5,h*.52),(w*.73,bottom),(right,top)])
        elif char=='X':
            # Curved negative-space transitions are distinctive even without dlig.
            stem=t*.95; cx=w/2; cy=h/2
            p=[(x,top),(x+stem*1.15,top),(cx,cy+stem*.55),(right-stem*1.15,top),(right,top),
               (cx+stem*.85,cy),(right,bottom),(right-stem*1.15,bottom),
               (cx,cy-stem*.55),(x+stem*1.15,bottom),(x,bottom),(cx-stem*.85,cy)]
            shape=Polygon(p).buffer(t*.22,quad_segs=8).buffer(-t*.22,quad_segs=8)
            shape=shape.buffer(-t*.16,quad_segs=8).buffer(t*.16,quad_segs=8)
        elif char=='Y': shape=union(self.stroke_path([(x,top),(w*.5,h*.46),(right,top)]),self.vbar(w*.5,bottom,h*.46))
        elif char=='Z': shape=self.stroke_path([(x,top),(right,top),(x,bottom),(right,bottom)])
        else: return self.punctuation(char)
        # Respect common cap/baseline boundaries while retaining designed width.
        shape=fit(soften(shape,t*self.rules['join_radius']),w,h)
        return affinity.translate(soften(shape,t*.18),xoff=28),w+56

    def lower(self,c):
        t=self.stroke*.86; h=550; w=560; cap=760
        ring=self.bowl(w,h,t)
        x=t*.58; right=w-t*.58
        if c in 'ao':
            shape=ring
            if c=='a':
                # Familiar double-storey a for document reading. Its lower
                # counter and soft right exit carry the custom construction.
                lower=self.bowl(w,h*.59,t,.39)
                arch=bezier([(t*.65,h*.79),(w*.29,h*1.02),(right,h*1.05),(right,h*.69)],24)
                shape=union(lower,self.stroke_path(arch,t*.82),
                    self.vbar(right,t*.10,h*.72,t),self.hbar(right,w+t*.12,t*.20,t*.62))
        elif c in 'bdpq':
            left=c in 'bp'; descent=c in 'pq'
            shape=union(ring,self.vbar(x if left else right,-180 if descent else t*.48,h-t*.5 if descent else cap-t*.5,t))
        elif c=='c': shape=ring.difference(box(w*.56,h*.26,w+20,h*.74))
        elif c=='e': shape=union(ring.difference(box(w*.58,h*.23,w+20,h*.51)),self.hbar(t*.6,w-t*.55,h*.52,t*.83))
        elif c=='g':
            if self.serif:
                upper=affinity.translate(self.bowl(w*.89,h*.69,t*.79),xoff=w*.02,yoff=h*.31)
                bottom=affinity.translate(self.bowl(w*.94,h*.44,t*.64),xoff=w*.03,yoff=-185)
                link=self.stroke_path(bezier([(w*.23,h*.36),(w*.04,h*.12),(w*.19,h*.03),(w*.69,h*.06)]),t*.58)
                shape=union(upper,bottom,link,self.hbar(w*.67,w-t*.14,h-t*.35,t*.54))
            else: shape=union(ring,self.vbar(right,-110,h-t*.5,t),self.hbar(w*.24,right,-150,t*.72))
        elif c in 'hnmur':
            if c=='m':
                w=830
                arch=self.bowl(w*.55,h,t*.92).difference(box(t*.9,-20,w*.55-t*.9,h*.47))
                shape=union(arch,affinity.translate(arch,xoff=w*.45))
            else:
                shape=ring.difference(box(t*.94,-20,w-t*.94,h*.43))
                if c=='h': shape=union(shape,self.vbar(x,t*.5,cap-t*.5,t))
                if c=='u': shape=affinity.scale(shape,xfact=1,yfact=-1,origin=(w/2,h/2))
                if c=='r': shape=shape.difference(box(w*.55,-30,w+20,h*.61))
        elif c in 'ilj':
            w=270
            shape=self.vbar(w*.5,0,cap if c=='l' else h,t)
            if c!='l': shape=union(shape,soft_box(w*.5-t*.55,cap-t,w*.5+t*.55,cap,t*.27))
            if c=='j': shape=union(shape,self.stroke_path([(w*.5,0),(w*.5,-120),(0,-155)],t*.82))
        elif c in 'ft':
            w=470
            shape=union(self.vbar(w*.43,t*.5,cap-t*.5,t),self.hbar(t*.15,w-t*.2,h*.77,t*.84))
            if c=='t': shape=union(shape,self.hbar(w*.43,w*.80,t*.5,t*.76))
            else: shape=union(shape,self.hbar(w*.43,w*.87,cap-t*.5,t*.8))
        elif c=='s': shape=self.s(w,h,t)
        elif c in 'kvwxyz':
            up,advance=self.latin(c.upper())
            shape=affinity.scale(up,xfact=.88,yfact=h/760,origin=(0,0))
            w=advance*.88-56
            if c=='k': shape=union(shape,self.vbar(x,0,cap-t*.5,t))
            if c=='y':
                shape=union(self.stroke_path([(t*.58,h-t*.5),(w*.5,h*.24),(w-t*.58,h-t*.5)],t*.90),
                    self.stroke_path([(w*.5,h*.24),(w*.28,-160),(w*.09,-180)],t*.80))
        else: return self.punctuation(c)
        # Text letters share real cap / x-height and descender boundaries at
        # every weight. Bracket expansion must not make a's sit above o's.
        if c in 'bdfhkli': body,baseline=760,0
        elif c=='t': body,baseline=660,0
        elif c=='j': body,baseline=940,-180
        elif c in 'gpqy': body,baseline=735,-185
        else: body,baseline=550,0
        shape=fit(shape,w,body,y=baseline)
        return affinity.translate(shape,xoff=28),w+56

    def digit(self,c):
        t=self.stroke*.92; w=580; h=760; x=t*.55; right=w-t*.55
        ring=self.bowl(w,h,t)
        if c=='0': shape=ring
        elif c=='1': shape=union(self.stroke_path([(w*.22,h*.79),(w*.55,h-t*.5),(w*.55,t*.5)],t),self.hbar(w*.20,w*.83,t*.5,t*.8))
        elif c=='2':
            p=bezier([(x,h*.77),(w*.22,h*1.02),(w*.91,h*1.04),(right,h*.70)])
            p+=bezier([(right,h*.70),(right,h*.45),(w*.28,h*.25),(x,t*.55)])[1:]
            shape=union(self.stroke_path(p,t),self.hbar(x,right,t*.55,t))
        elif c=='3':
            p=bezier([(x,h*.88),(w*1.03,h*1.07),(w*1.03,h*.49),(w*.39,h*.5)])
            p+=bezier([(w*.39,h*.5),(w*1.05,h*.53),(w*1.06,-h*.12),(x,h*.12)])[1:]
            shape=self.stroke_path(p,t)
        elif c=='4': shape=union(self.stroke_path([(w*.73,h-t*.5),(x,h*.34),(right,h*.34)],t),self.vbar(w*.73,t*.5,h-t*.5,t))
        elif c=='5': shape=union(self.stroke_path([(right,h-t*.5),(x,h-t*.5),(x,h*.54),(w*.46,h*.54)],t),self.bowl(w,h*.56,t).difference(box(-20,h*.18,w*.27,h*.54)))
        elif c=='6': shape=union(self.bowl(w,h*.62,t),self.stroke_path([(w*.85,h*.88),(w*.39,h-t*.5),(x,h*.66),(x,h*.32)],t))
        elif c=='7': shape=self.stroke_path([(x,h-t*.5),(right,h-t*.5),(w*.29,t*.5)],t)
        elif c=='8': shape=union(self.bowl(w,h*.53,t),affinity.translate(self.bowl(w*.94,h*.51,t),xoff=w*.03,yoff=h*.49))
        else:
            six,_=self.digit('6'); shape=affinity.rotate(affinity.translate(six,xoff=-28),180,origin=(w/2,h/2))
        return affinity.translate(fit(shape,w,h),xoff=28),w+56

    def punctuation(self,c):
        t=self.stroke*.72; h=760
        dot=soft_box(35,0,35+t,t,t*.27)
        if c in '.·:;!¡?¿':
            if c=='.': return dot,t+75
            if c=='·': return affinity.translate(dot,yoff=340),t+75
            if c in ':;':
                shape=union(affinity.translate(dot,yoff=150),affinity.translate(dot,yoff=450))
                if c==';': shape=union(shape,self.stroke_path([(35+t*.55,170),(25,80)],t*.45))
                return shape,t+75
            if c in '!¡':
                shape=union(dot,self.vbar(35+t*.5,230,760,t))
                if c=='¡': shape=affinity.rotate(shape,180,origin=(70,380))
                return shape,t+75
            q=self.stroke_path(bezier([(55,620),(90,860),(570,820),(500,580)])+[(280,380),(280,230)],t)
            return union(q,affinity.translate(dot,xoff=210)),590
        if c in '-–—_=':
            width={'-':400,'–':620,'—':900,'_':620,'=':620}[c]
            shape=self.hbar(35,width-35,80 if c=='_' else 350,t*.70)
            if c=='=': shape=union(self.hbar(35,width-35,230,t*.70),self.hbar(35,width-35,470,t*.70))
            return shape,width
        if c in ',': return union(dot,self.stroke_path([(50+t*.45,25),(30,-110)],t*.5)),t+75
        if c in "'‘’\"“”`":
            quote=self.stroke_path([(70,740),(35,610)],t*.70)
            if c in '\"“”': quote=union(quote,affinity.translate(quote,xoff=t+35))
            if c in '‘“': quote=affinity.scale(quote,xfact=-1,yfact=-1,origin=quote.centroid)
            return quote,(t+80)*(2 if c in '\"“”' else 1)
        if c in '()[]{}<>':
            p=bezier([(240,780),(-20,640),(-20,120),(240,-50)])
            if c in '[]': p=[(240,780),(60,780),(60,-50),(240,-50)]
            if c in '{}': p=[(250,780),(130,720),(130,440),(45,365),(130,290),(130,15),(250,-50)]
            if c in '<>': p=[(270,710),(50,360),(270,20)]
            shape=self.stroke_path(p,t*.8)
            if c in ')]}>': shape=affinity.scale(shape,xfact=-1,yfact=1,origin=(150,0))
            return shape,360
        if c in '/\\|':
            p=[(60,-20),(460,770)] if c=='/' else [(460,-20),(60,770)]
            if c=='|': p=[(200,-60),(200,820)]
            return self.stroke_path(p,t*.8),520 if c!='|' else 400
        if c in '+×':
            shape=union(self.hbar(55,580,350,t),self.vbar(317,90,610,t))
            if c=='×': shape=affinity.rotate(shape,45,origin=(317,350))
            return shape,640
        if c in '₩$€£¥':
            letter={'₩':'W','$':'S','€':'C','£':'L','¥':'Y'}[c]
            shape,width=self.latin(letter)
            if c=='$': shape=union(shape,self.vbar(width*.48,-45,805,t*.45))
            else: shape=union(shape,self.hbar(15,width-15,320,t*.38),self.hbar(15,width-15,470,t*.38))
            return shape,width
        if c=='…': return union(dot,affinity.translate(dot,xoff=t+70),affinity.translate(dot,xoff=2*t+140)),3*t+230
        if c=='%':
            small=self.bowl(210,230,t*.55)
            return union(affinity.translate(small,yoff=520),affinity.translate(small,xoff=440),self.stroke_path([(60,0),(600,760)],t*.7)),710
        if c=='^': return self.stroke_path([(55,470),(310,750),(565,470)],t*.8),640
        if c=='~': return self.stroke_path(bezier([(40,330),(190,550),(400,150),(590,370)]),t*.8),660
        if c in '#*':
            shape=union(self.vbar(180,20,700,t*.7),self.vbar(440,20,700,t*.7),self.hbar(40,580,260,t*.7),self.hbar(40,580,480,t*.7))
            if c=='*': shape=union(self.stroke_path([(310,350),(310,730)],t*.7),self.stroke_path([(100,410),(520,690)],t*.7),self.stroke_path([(100,690),(520,410)],t*.7))
            return shape,640
        if c=='@':
            a,_=self.lower('a')
            return union(self.bowl(860,800,t*.65),affinity.translate(affinity.scale(a,.68,.68,origin=(0,0)),xoff=170,yoff=220)),930
        if c=='&':
            shape=union(self.bowl(640,430,t),affinity.translate(self.bowl(480,420,t),xoff=60,yoff=340),self.stroke_path([(130,620),(650,0)],t*.8))
            return shape,750
        # A visible custom missing-character marker, never a borrowed fallback.
        return self.bowl(620,760,t),700

    def consonant(self,j,w,h,base=None):
        base=base or self.stroke*.78
        if j in DOUBLE or j in CLUSTERS:
            pair=(DOUBLE[j],DOUBLE[j]) if j in DOUBLE else CLUSTERS[j]
            gap=max(18,w*.07); width=(w-gap)/2
            a=self.consonant(pair[0],width,h,base*.79)
            b=self.consonant(pair[1],width,h,base*.79)
            return union(a,affinity.translate(b,xoff=width+gap))
        factor=(min(w,h)/550)**.18
        t=base*factor
        maximum=min(w,h)*(.22 if j=='ㅂ' else .38)
        # A smooth optical limit retains four genuinely different strokes in
        # compact components. Hard min() made Bold and ExtraBold identical.
        t=t/(1+(t/maximum)**4)**.25
        x=t*.52; right=w-t*.52; low=t*.52; high=h-t*.52
        if j=='ㄱ': shape=self.stroke_path([(x,high),(right,high),(right,low)],t)
        elif j=='ㄴ': shape=self.stroke_path([(x,high),(x,low),(right,low)],t)
        elif j=='ㄷ': shape=self.stroke_path([(right,high),(x,high),(x,low),(right,low)],t)
        elif j=='ㄹ':
            # Three horizontal rows need their own optical thickness. Closing
            # the two open spaces would turn a jongseong rieul into a block.
            rt=t*.74;rt=rt/(1+(rt/(h*.195))**4)**.25
            left=rt*.55;rr=w-rt*.55;ll=rt*.55;hh=h-rt*.55
            shape=self.stroke_path([(left,hh),(rr,hh),(rr,h*.5),(left,h*.5),(left,ll),(rr,ll)],rt)
        elif j=='ㅁ': shape=self.bowl(w,h,t,.22)
        elif j=='ㅂ': shape=union(self.bowl(w,h,t,.15),self.hbar(t*.5,w-t*.5,h*.5,t*.88))
        elif j in 'ㅅㅈㅊ':
            # Sculpted shoulder / concave junction derived from the X's waist.
            p=bezier([(x,low),(w*.23,h*.30),(w*.40,h*.69),(w*.5,high)])
            q=bezier([(w*.5,high),(w*.60,h*.69),(w*.77,h*.30),(right,low)])
            shape=union(self.stroke_path(p,t),self.stroke_path(q,t))
            if j=='ㅈ':
                shape=fit(shape,w,h*.82)
                shape=union(shape,self.hbar(t*.5,w-t*.5,h*.82,t*.75))
            if j=='ㅊ':
                shape=fit(shape,w,h*.65)
                shape=union(shape,self.hbar(t*.5,w-t*.5,h*.65,t*.75),
                    self.hbar(w*.32,w*.68,h*.94,t*.30))
        elif j=='ㅇ': shape=self.bowl(w,h,t,.36)
        elif j=='ㅋ':
            ht=t*.72;ht=ht/(1+(ht/(h*.22))**4)**.25
            shape=union(self.vbar(right,ht*.5,h-ht*.5,t),
                self.hbar(x,right,h-ht*.5,ht),self.hbar(x,right,h*.5,ht))
        elif j=='ㅌ':
            ht=t*.70;ht=ht/(1+(ht/(h*.185))**4)**.25
            shape=union(self.vbar(x,ht*.5,h-ht*.5,t),
                self.hbar(x,right,h-ht*.5,ht),self.hbar(x,right,h*.5,ht),
                self.hbar(x,right,ht*.5,ht))
        elif j=='ㅍ':
            limit=min(h*.22,w*.24);t=t/(1+(t/limit)**4)**.25
            shape=union(self.hbar(t*.5,w-t*.5,h-t*.5,t*.90),self.hbar(t*.5,w-t*.5,t*.5,t*.90),
                        self.vbar(w*.25,t*.5,h-t*.5,t),self.vbar(w*.75,t*.5,h-t*.5,t))
        elif j=='ㅎ':
            ct=t*.86;ct=ct/(1+(ct/(h*.52*.32))**4)**.25
            circle=self.bowl(w,h*.52,ct,.38)
            shape=union(circle,self.hbar(t*.5,w-t*.5,h*.72,t*.54),
                self.hbar(w*.31,w*.69,h*.95,t*.24))
        else: raise ValueError(j)
        return fit(soften(shape,min(t*.17,h*.028,w*.028)),w,h)

    def vowel(self,j,w,h):
        if j in ('ㅐ','ㅒ','ㅔ','ㅖ'):
            first={'ㅐ':'ㅏ','ㅒ':'ㅑ','ㅔ':'ㅓ','ㅖ':'ㅕ'}[j]
            return union(self.vowel(first,w*.66,h),affinity.translate(self.vowel('ㅣ',w*.19,h),xoff=w*.81))
        t=self.stroke*.74*(min(w,h)/450)**.14
        limit=min(w*.58,h*.44);t=t/(1+(t/limit)**4)**.25
        if j in VERTICAL:
            center=w*.36 if j in 'ㅏㅑ' else w*.64 if j in 'ㅓㅕ' else w*.70
            shape=self.vbar(center,t*.5,h-t*.5,t)
            if j!='ㅣ':
                yy=[h*.39,h*.66] if j in 'ㅑㅕ' else [h*.52]
                for y in yy:
                    shape=union(shape,self.hbar(center,w-t*.45,y,t*.86) if j in 'ㅏㅑ' else self.hbar(t*.45,center,y,t*.86))
        elif j in HORIZONTAL:
            yy=h*.27 if j in 'ㅗㅛ' else h*.73 if j in 'ㅜㅠ' else h*.5
            shape=self.hbar(t*.45,w-t*.45,yy,t*.90)
            if j!='ㅡ':
                xx=[w*.36,w*.66] if j in 'ㅛㅠ' else [w*.5]
                for x in xx: shape=union(shape,self.vbar(x,yy,h-t*.45,t) if j in 'ㅗㅛ' else self.vbar(x,t*.45,yy,t))
        else: raise ValueError(j)
        # A single vertical / horizontal vowel must retain its actual stroke;
        # stretching its ink bounds to the entire cell would erase weights.
        return shape

    def combined_vowel(self,j,has_final):
        horizontal,vertical={'ㅘ':('ㅗ','ㅏ'),'ㅙ':('ㅗ','ㅐ'),'ㅚ':('ㅗ','ㅣ'),
                             'ㅝ':('ㅜ','ㅓ'),'ㅞ':('ㅜ','ㅔ'),'ㅟ':('ㅜ','ㅣ'),'ㅢ':('ㅡ','ㅣ')}[j]
        low_y=295 if has_final else 40
        horizontal_shape=affinity.translate(self.vowel(horizontal,560,155 if has_final else 255),xoff=75,yoff=low_y)
        vertical_shape=affinity.translate(self.vowel(vertical,245,500 if has_final else 770),xoff=725,yoff=low_y)
        return union(horizontal_shape,vertical_shape)


def component_shapes(design):
    """Build position-specific outlines once, shared by all 11,172 syllables."""
    shapes={}; layouts={}
    for form in ('v','h','m'):
        for final in (False,True):
            if form=='v':
                initial=(65,315,450,505) if final else (65,10,450,810)
                vowel=(645,315,300,505) if final else (645,10,300,810)
            elif form=='h':
                initial=(180,475,680,345) if final else (180,350,680,470)
                vowel=(70,270,910,155) if final else (70,5,910,260)
            else:
                initial=(70,490,510,330) if final else (70,410,510,410)
                vowel=None
            suffix=f'{form}{int(final)}'
            for index,j in enumerate(INITIALS):
                x,y,w,h=initial
                shapes[f'cho.{suffix}.{index}']=affinity.translate(design.consonant(j,w,h),xoff=x,yoff=y)
            for index,j in enumerate(VOWELS):
                actual='v' if j in VERTICAL else 'h' if j in HORIZONTAL else 'm'
                if actual!=form: continue
                if vowel:
                    x,y,w,h=vowel
                    shape=affinity.translate(design.vowel(j,w,h),xoff=x,yoff=y)
                else: shape=design.combined_vowel(j,final)
                shapes[f'jung.{suffix}.{index}']=shape
            layouts[(form,final)]=suffix
    for index,j in enumerate(FINALS[1:],1):
        shapes[f'jong.{index}']=affinity.translate(design.consonant(j,810,240,design.stroke*.70),xoff=115,yoff=-10)
    return shapes,layouts


def to_glyph(shape):
    pen=TTGlyphPen(None)
    if shape.is_empty: return pen.glyph()
    if not shape.is_valid: shape=shape.buffer(0)
    polygons=[shape] if shape.geom_type=='Polygon' else [g for g in shape.geoms if g.geom_type=='Polygon']
    for polygon in polygons:
        polygon=orient(polygon,sign=-1)
        for ring in [polygon.exterior,*polygon.interiors]:
            coordinates=list(ring.coords)[:-1]
            # Integer snapping is final quantization, not a source-font warp.
            points=[]
            for x,y in coordinates:
                pt=(round(x),round(y))
                if not points or pt!=points[-1]: points.append(pt)
            if len(points)<3: continue
            if points[-1]==points[0]: points.pop()
            pen.moveTo(points[0])
            for pt in points[1:]: pen.lineTo(pt)
            pen.closePath()
    return pen.glyph()
