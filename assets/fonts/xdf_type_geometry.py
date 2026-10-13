# SPDX-License-Identifier: OFL-1.1
"""XDF Type 0.300 original text outlines. No upstream typeface outlines.
Hangul components are drawn at their intended size, never stretched to fill a
box. The XDF logo is an optional ligature, separate from normal text X/D/F.
"""
from __future__ import annotations
import math
from shapely import affinity
from shapely.geometry import Polygon, LineString, Point, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from fontTools.pens.ttGlyphPen import TTGlyphPen

WEIGHTS={300:68,500:100,700:145,800:175}
FAMILY_RULES={
 'xebatang':{'join_radius':.065,'horizontal_ratio':.52,'bowl_horizontal_ratio':.58,'serif_span':.34,'bar_tip':.055},
 'xedotum':{'join_radius':.075,'horizontal_ratio':.94,'bowl_horizontal_ratio':.91,'serif_span':0,'bar_tip':.055}}
INITIALS=list('ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ')
VOWELS=list('ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ')
FINALS=['']+list('ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ')
VERTICAL=set('ㅏㅐㅑㅒㅓㅔㅕㅖㅣ')
HORIZONTAL=set('ㅗㅛㅜㅠㅡ')
DOUBLE=dict(zip('ㄲㄸㅃㅆㅉ','ㄱㄷㅂㅅㅈ'))
CLUSTERS=dict(zip('ㄳㄵㄶㄺㄻㄼㄽㄾㄿㅀㅄ',[('ㄱ','ㅅ'),('ㄴ','ㅈ'),('ㄴ','ㅎ'),('ㄹ','ㄱ'),('ㄹ','ㅁ'),('ㄹ','ㅂ'),('ㄹ','ㅅ'),('ㄹ','ㅌ'),('ㄹ','ㅍ'),('ㄹ','ㅎ'),('ㅂ','ㅅ')]))

def union(*items):
 return unary_union([g for g in items if g is not None and not g.is_empty])

def bezier(points,steps=40):
 a,b,c,d=points
 return [tuple((1-t)**3*a[k]+3*(1-t)**2*t*b[k]+3*(1-t)*t*t*c[k]+t**3*d[k] for k in range(2)) for t in [i/steps for i in range(steps+1)]]

def curves(*segments):
 points=[]
 for seg in segments:
  part=bezier(seg);points+=part if not points else part[1:]
 return points

def soft_box(x0,y0,x1,y1,r):
 if x1<=x0 or y1<=y0:return Polygon()
 r=max(0,min(r,(x1-x0)/2-.01,(y1-y0)/2-.01))
 return box(x0+r,y0+r,x1-r,y1-r).buffer(r,quad_segs=12) if r>.1 else box(x0,y0,x1,y1)

def ellipse(w,h,x=0,y=0):
 if w<=0 or h<=0:return Polygon()
 return affinity.translate(affinity.scale(Point(0,0).buffer(1,quad_segs=64),w/2,h/2,origin=(0,0)),xoff=x+w/2,yoff=y+h/2)

def fit(shape,w,h,x=0,y=0):
 """Only for isolated punctuation. Never used for Hangul text components."""
 if shape.is_empty:return shape
 a,b,c,d=shape.bounds
 return affinity.translate(affinity.scale(shape,w/(c-a),h/(d-b),origin=(a,b)),xoff=x-a,yoff=y-b)

def limit_stroke(t,maximum):return t/(1+(t/maximum)**6)**(1/6)

class Design:
 def __init__(self,family,weight):
  self.family=family;self.serif=family=='xebatang';self.rules=FAMILY_RULES[family];self.stroke=WEIGHTS[weight]

 def round_join(self,shape,t):
  r=t*self.rules['join_radius']
  return shape.buffer(r,quad_segs=8).buffer(-r,quad_segs=8)

 def stroke_path(self,points,t=None,tips=True):
  t=self.stroke if t is None else t
  if not self.serif:return LineString(points).buffer(t/2,cap_style='flat',join_style='round',quad_segs=12)
  # Stroke contrast follows the path direction, not anisotropic glyph scaling.
  left=[];right=[]
  for i,(x,y) in enumerate(points):
   a=points[max(0,i-1)];b=points[min(len(points)-1,i+1)]
   dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy)
   if length<.001:continue
   half=t*(.54+.46*abs(dy)/length)/2
   nx,ny=-dy/length*half,dx/length*half
   left.append((x+nx,y+ny));right.append((x-nx,y-ny))
  shape=Polygon(left+right[::-1])
  return shape if shape.is_valid else shape.buffer(0)

 def hbar(self,x0,x1,y,t=None,serifs=False):
  t=(self.stroke if t is None else t)*self.rules['horizontal_ratio'];cut=min(6,t*.055)
  shape=Polygon([(x0,y-t/2),(x1-cut,y-t/2),(x1,y-t*.32),(x1,y+t/2),(x0,y+t/2)])
  if self.serif and serifs:shape=union(shape,box(x1-t*.38,y-t*.70,x1+t*.10,y+t*.85))
  return shape

 def vbar(self,x,y0,y1,t=None,serifs=None):
  t=self.stroke if t is None else t;shape=box(x-t/2,y0,x+t/2,y1)
  if self.serif and serifs is not False:
   ext=12+t*.30;thick=max(12,t*.20)
   for y,sign in [(y0,1),(y1,-1)]:
    shape=union(shape,Polygon([(x-t/2-ext,y),(x+t/2+ext,y),(x+t/2+ext,y+sign*thick),(x+t/2+6,y+sign*thick),(x+t/2,y+sign*(thick+12)),(x-t/2,y+sign*(thick+12)),(x-t/2-6,y+sign*thick),(x-t/2-ext,y+sign*thick)]))
  return shape

 def bowl(self,w,h,t=None,roundness=None):
  t=self.stroke if t is None else t;ty=t*self.rules['bowl_horizontal_ratio']
  if roundness is None:outer=ellipse(w,h);inner=ellipse(w-2*t,h-2*ty,t,ty)
  else:
   r=min(w,h)*roundness;outer=soft_box(0,0,w,h,r);inner=soft_box(t,ty,w-t,h-ty,max(2,r-t*.55))
  return outer.difference(inner)

 def s(self,w,h,t=None):
  t=self.stroke if t is None else t
  points=curves([(w-t*.6,h*.82),(w*.71,h*1.04),(t*.3,h*.98),(t*.53,h*.72)],[(t*.53,h*.72),(t*.3,h*.51),(w*.78,h*.47),(w-t*.55,h*.27)],[(w-t*.55,h*.27),(w*1.04,h*.02),(w*.24,-h*.04),(t*.6,h*.16)])
  low=min(y for x,y in points);high=max(y for x,y in points)
  edge=t*(.27 if self.serif else .5)
  points=[(x,edge+(y-low)*(h-edge*2)/(high-low)) for x,y in points]
  return self.stroke_path(points,t)

 def d_bowl(self,w,h,t):
  ty=t*self.rules['bowl_horizontal_ratio']
  outer=Polygon([(0,0),(w*.39,0)]+curves([(w*.39,0),(w*.80,0),(w,h*.16),(w,h*.5)],[(w,h*.5),(w,h*.84),(w*.80,h),(w*.39,h)])[1:]+[(0,h)])
  iw=w-t*2;ih=h-ty*2
  inner=Polygon([(t,ty),(t+iw*.36,ty)]+curves([(t+iw*.36,ty),(t+iw*.80,ty),(w-t,ty+ih*.16),(w-t,h*.5)],[(w-t,h*.5),(w-t,h-ty-ih*.16),(t+iw*.8,h-ty),(t+iw*.36,h-ty)])[1:]+[(t,h-ty)])
  return outer.difference(inner)

 def latin(self,c):
  if c==' ':return Polygon(),300
  if c.islower():return self.lower(c)
  if c.isdigit():return self.digit(c)
  if not 'A'<=c<='Z':return self.punctuation(c)
  t=self.stroke;h=760
  w={'A':650,'B':605,'C':650,'D':665,'E':535,'F':510,'G':675,'H':650,'I':190 if not self.serif else 285,'J':435,'K':600,'L':495,'M':800,'N':655,'O':700,'P':580,'Q':700,'R':625,'S':560,'T':600,'U':645,'V':645,'W':890,'X':625,'Y':610,'Z':570}[c]
  x=t/2;rr=w-t/2;lo=t/2;hi=h-t/2;mid=w/2;dt=t*.94
  if c=='A':shape=union(self.stroke_path([(x,lo),(mid,hi),(rr,lo)],dt),self.hbar(w*.23,w*.77,h*.34,t))
  elif c=='B':shape=union(self.d_bowl(w,h*.52,t),affinity.translate(self.d_bowl(w*.94,h*.51,t),yoff=h*.49),self.vbar(x,0,h,t))
  elif c in 'COGQ':
   shape=self.bowl(w,h)
   if c in 'CG':
    shape=shape.difference(Polygon([(w*.5,h*.5),(w+30,h*.78),(w+30,h*.22)]))
    if c=='G':shape=union(shape,self.hbar(w*.54,w,h*.43,t),self.vbar(rr,h*.18,h*.45,t,False))
   elif c=='Q':shape=union(shape,self.stroke_path([(w*.58,h*.19),(w*.97,-15)],t*.77))
  elif c=='D':shape=union(self.d_bowl(w,h,t),self.vbar(x,0,h,t))
  elif c in 'EF':
   shape=union(self.vbar(x,0,h,t),self.hbar(0,w,h-t*self.rules['horizontal_ratio']/2,t,True),self.hbar(0,w*.81,h*.48,t,True))
   if c=='E':shape=union(shape,self.hbar(0,w,t*self.rules['horizontal_ratio']/2,t,True))
  elif c=='H':shape=union(self.vbar(x,0,h,t),self.vbar(rr,0,h,t),self.hbar(x,rr,h*.48,t))
  elif c=='I':shape=self.vbar(mid,0,h,t)
  elif c=='J':shape=self.stroke_path(curves([(rr,h),(rr,h*.66),(rr,h*.39),(rr,h*.25)],[(rr,h*.25),(rr,h*.04),(w*.23,-25),(x,h*.17)]),t)
  elif c=='K':shape=union(self.vbar(x,0,h,t),self.stroke_path([(rr,hi),(x,h*.43)],dt),self.stroke_path([(w*.42,h*.55),(rr,lo)],dt))
  elif c=='L':shape=union(self.vbar(x,0,h,t),self.hbar(0,w,t*self.rules['horizontal_ratio']/2,t,True))
  elif c=='M':shape=union(self.vbar(x,0,h,t),self.vbar(rr,0,h,t),self.stroke_path([(x,hi),(mid,h*.30),(rr,hi)],dt))
  elif c=='N':shape=union(self.vbar(x,0,h,t),self.vbar(rr,0,h,t),self.stroke_path([(x,hi),(rr,lo)],dt))
  elif c in 'PR':
   shape=union(self.vbar(x,0,h,t),affinity.translate(self.d_bowl(w,h*.55,t),yoff=h*.45))
   if c=='R':shape=union(shape,self.stroke_path([(w*.44,h*.46),(rr,lo)],dt))
  elif c=='S':shape=self.s(w,h,t)
  elif c=='T':shape=union(self.vbar(mid,0,h-t*.4,t),self.hbar(0,w,h-t*self.rules['horizontal_ratio']/2,t,True))
  elif c=='U':shape=self.stroke_path(curves([(x,h),(x,h*.70),(x,h*.41),(x,h*.28)],[(x,h*.28),(x,-h*.04),(rr,-h*.04),(rr,h*.28)],[(rr,h*.28),(rr,h*.41),(rr,h*.70),(rr,h)]),t)
  elif c=='V':shape=self.stroke_path([(x,hi),(mid,lo),(rr,hi)],dt)
  elif c=='W':shape=self.stroke_path([(x,hi),(w*.25,lo),(mid,h*.64),(w*.75,lo),(rr,hi)],dt)
  elif c=='X':shape=union(self.stroke_path(curves([(x,hi),(w*.25,h*.62),(w*.71,h*.37),(rr,lo)]),dt),self.stroke_path(curves([(x,lo),(w*.27,h*.37),(w*.72,h*.63),(rr,hi)]),dt))
  elif c=='Y':shape=union(self.stroke_path([(x,hi),(mid,h*.44),(rr,hi)],dt),self.vbar(mid,0,h*.46,t))
  else:shape=union(self.hbar(0,w,h-t*self.rules['horizontal_ratio']/2,t,True),self.hbar(0,w,t*self.rules['horizontal_ratio']/2,t,True),self.stroke_path([(rr,hi),(x,lo)],dt))
  if self.serif and c in 'AVWXY':
   ext=14+t*.26;th=max(12,t*.18)
   for px,py in [(x,0),(rr,0),(x,h-th),(rr,h-th)]:shape=union(shape,box(px-t*.45-ext,py,px+t*.45+ext,py+th))
  side=45 if self.serif else 40
  return affinity.translate(self.round_join(shape,t),xoff=side),w+side*2

 def lower(self,c):
  t=self.stroke*.92;h=550;cap=760
  w={'a':510,'b':540,'c':485,'d':540,'e':520,'f':310,'g':530,'h':535,'i':135,'j':170,'k':475,'l':135,'m':810,'n':535,'o':550,'p':540,'q':540,'r':330,'s':435,'t':325,'u':535,'v':510,'w':730,'x':495,'y':510,'z':455}[c]
  x=t/2;rr=w-t/2
  if c=='o':shape=self.bowl(w,h,t)
  elif c in 'bdpq':shape=union(self.bowl(w,h,t),self.vbar(x if c in 'bp' else rr,-185 if c in 'pq' else 0,h if c in 'pq' else cap,t))
  elif c=='a':
   arch=curves([(t*.55,h*.82),(w*.32,h*1.00),(rr,h*1.00),(rr,h*.65)])
   shift=h-t*.87*(.27 if self.serif else .5)-max(y for x,y in arch)
   arch=[(x,y+shift) for x,y in arch]
   shape=union(self.bowl(w,h*.60,t),self.stroke_path(arch,t*.87),self.vbar(rr,0,h*.69,t))
  elif c=='c':shape=self.bowl(w,h,t).difference(Polygon([(w*.5,h*.5),(w+20,h*.78),(w+20,h*.22)]))
  elif c=='e':shape=union(self.bowl(w,h,t).difference(box(w*.50,h*.19,w+20,h*.48)),self.hbar(t*.4,w-t*.3,h*.51,t*.88))
  elif c=='g':
   if self.serif:
    top=affinity.translate(self.bowl(w*.88,h*.64,t*.9),xoff=w*.03,yoff=h*.36)
    bottom=affinity.translate(self.bowl(w*.94,h*.38,t*.66),xoff=w*.03,yoff=-185)
    link=self.stroke_path(curves([(w*.22,h*.37),(w*.04,h*.13),(w*.18,h*.03),(w*.64,h*.05)]),t*.50)
    shape=union(top,bottom,link,self.hbar(w*.67,w,h-t*.25,t*.48))
   else:shape=union(self.bowl(w,h,t),self.vbar(rr,h*.24,h,t),self.stroke_path(curves([(rr,h*.25),(rr,0),(rr,-185),(w*.5,-185)],[(w*.5,-185),(w*.29,-185),(w*.18,-155),(t*.5,-120)]),t*.90))
  elif c in 'hnmr':
   def arch(width):
    crown=(h-t*(.27 if self.serif else .5)-h*.10625)/.75
    return self.stroke_path(curves([(x,0),(x,h*.21),(x,h*.41),(x,h*.43)],[(x,h*.43),(width*.24,crown),(width-t*.5,crown),(width-t*.5,h*.42)],[(width-t*.5,h*.42),(width-t*.5,h*.25),(width-t*.5,h*.1),(width-t*.5,0)]),t)
   if c=='m':
    aw=w*.53;shape=union(arch(aw),affinity.translate(arch(aw),xoff=w-aw),self.vbar(x,0,h,t))
   elif c=='r':
    shoulder=curves([(x,h*.38),(w*.25,h*.95),(w*.69,h*1.02),(w,h*.83)])
    shift=h-t*.90*(.27 if self.serif else .5)-max(y for x,y in shoulder)
    shoulder=[(x,y+shift) for x,y in shoulder]
    shape=union(self.vbar(x,0,h,t),self.stroke_path(shoulder,t*.90))
   else:shape=union(arch(w),self.vbar(x,0,cap if c=='h' else h,t))
  elif c=='u':
   foot=(t*(.27 if self.serif else .5)-h*.075)/.75
   shape=union(self.stroke_path(curves([(x,h),(x,h*.80),(x,h*.45),(x,h*.30)],[(x,h*.30),(x,foot),(rr,foot),(rr,h*.30)],[(rr,h*.30),(rr,h*.45),(rr,h*.80),(rr,h)]),t),self.vbar(rr,0,h,t))
  elif c in 'ilj':
   cx=w/2;shape=self.vbar(cx,0,cap if c=='l' else h,t)
   if c!='l':shape=union(shape,ellipse(t*1.08,t*1.08,cx-t*.54,cap-t*1.08))
   if c=='j':shape=union(shape,self.stroke_path(curves([(cx,50),(cx,-170),(cx,-185),(-45,-185)]),t))
  elif c in 'ft':
   cx=w*.43
   if c=='f':
    hook=curves([(cx,cap*.78),(cx,cap),(w*.72,cap),(w,cap-t*.55)])
    shift=cap-t*(.27 if self.serif else .5)-max(y for x,y in hook)
    hook=[(x,y+shift) for x,y in hook]
    shape=union(self.vbar(cx,0,hook[0][1],t,False),self.stroke_path(hook,t))
   else:shape=self.stroke_path(curves([(cx,690),(cx,470),(cx,225),(cx,t*1.1)],[(cx,t*1.1),(cx,-10),(w*.69,-10),(w,t*.46)]),t)
   shape=union(shape,self.hbar(0,w,h*.79,t*.87))
  elif c=='s':shape=self.s(w,h,t)
  elif c=='k':shape=union(self.vbar(x,0,cap,t),self.stroke_path([(rr,h-t*.5),(x,h*.23)],t*.88),self.stroke_path([(w*.38,h*.38),(rr,t*.5)],t*.90))
  elif c=='v':shape=self.stroke_path([(x,h-t*.5),(w/2,t*.5),(rr,h-t*.5)],t*.94)
  elif c=='w':shape=self.stroke_path([(x,h-t*.5),(w*.25,t*.5),(w*.5,h*.61),(w*.75,t*.5),(rr,h-t*.5)],t*.90)
  elif c=='x':shape=union(self.stroke_path([(x,h-t*.5),(rr,t*.5)],t*.92),self.stroke_path([(x,t*.5),(rr,h-t*.5)],t*.92))
  elif c=='y':shape=union(self.stroke_path([(x,h-t*.5),(w*.53,t*.7)],t*.90),self.stroke_path(curves([(rr,h-t*.5),(w*.59,h*.32),(w*.43,-185),(w*.13,-185)]),t*.90))
  else:shape=union(self.hbar(0,w,h-t*self.rules['horizontal_ratio']/2,t),self.hbar(0,w,t*self.rules['horizontal_ratio']/2,t),self.stroke_path([(rr,h-t*.5),(x,t*.5)],t*.88))
  # Optical overshoot is deliberate; this is not forced ink-box resizing.
  return affinity.translate(self.round_join(shape,t),xoff=38),w+76

 def digit(self,c):
  t=self.stroke*.93;w=540;h=760;x=t*.55;rr=w-t*.55
  if c=='0':shape=self.bowl(w,h,t)
  elif c=='1':shape=union(self.stroke_path([(w*.18,h*.78),(w*.55,h-t*.5),(w*.55,0)],t),self.hbar(w*.20,w*.83,t*.3,t*.75))
  elif c=='2':shape=union(self.stroke_path(curves([(x,h*.77),(w*.23,h*1.05),(rr,h*1.04),(rr,h*.70)],[(rr,h*.70),(rr,h*.47),(w*.26,h*.20),(x,t*.48)]),t),self.hbar(x,rr,t*.48,t))
  elif c=='3':shape=self.stroke_path(curves([(x,h*.84),(w*.96,h*1.12),(w*1.02,h*.48),(w*.38,h*.51)],[(w*.38,h*.51),(w*1.05,h*.54),(w*1.07,-h*.16),(x,h*.14)]),t)
  elif c=='4':shape=union(self.stroke_path([(w*.74,h-t*.5),(x,h*.33),(rr,h*.33)],t),self.vbar(w*.74,0,h,t))
  elif c=='5':shape=union(self.stroke_path([(rr,h-t*.5),(t*.65,h-t*.5),(t*.65,h*.54)],t),self.stroke_path(curves([(t*.65,h*.54),(w*.91,h*.68),(w*1.04,h*.23),(rr,h*.19)],[(rr,h*.19),(w*.73,-h*.05),(w*.23,0),(x,h*.15)]),t))
  elif c=='6':shape=union(self.bowl(w,h*.57,t),self.stroke_path(curves([(w*.86,h*.86),(w*.21,h*1.08),(x,h*.61),(x,h*.30)]),t))
  elif c=='7':shape=self.stroke_path([(x,h-t*.5),(rr,h-t*.5),(w*.30,0)],t)
  elif c=='8':shape=union(self.bowl(w,h*.52,t),affinity.translate(self.bowl(w*.89,h*.49,t),xoff=w*.055,yoff=h*.51))
  else:
   six,_=self.digit('6');shape=affinity.rotate(affinity.translate(six,xoff=-40),180,origin=(w/2,h/2))
  return affinity.translate(self.round_join(shape,t),xoff=40),620

 def consonant(self,j,w,h,base=None):
  base=self.stroke*.90 if base is None else base
  if j in DOUBLE or j in CLUSTERS:
   pair=(DOUBLE[j],)*2 if j in DOUBLE else CLUSTERS[j];gap=28;cw=(w-gap)/2
   return union(self.consonant(pair[0],cw,h,base*.88),affinity.translate(self.consonant(pair[1],cw,h,base*.88),xoff=cw+gap))
  density=.235 if j in 'ㄹㅌㅂㅎ' else .32 if j=='ㅍ' else .36
  t=limit_stroke(base,min(w*.32,h*density));ht=t*self.rules['horizontal_ratio']
  x=t/2;rr=w-t/2;lo=ht/2;hi=h-ht/2
  def v(xx,y0,y1):
   stem=box(xx-t/2,y0,xx+t/2,y1)
   if self.serif:
    e=6+t*.15
    stem=union(stem,Polygon([(xx-t/2-e,y1),(xx+t/2,y1+ht*.35),(xx+t/2,y1-t*.22),(xx-t/2,y1-t*.26)]),Polygon([(xx-t/2,y0+ht),(xx+t/2,y0+ht),(xx+t/2+e,y0),(xx-t/2-e*.5,y0)]))
   return stem
  def hb(x0,x1,yy):
   bar=self.hbar(x0,x1,yy,t)
   if self.serif:bar=union(bar,Polygon([(x0,yy+ht/2),(x0+ht*.48,yy+ht*.88),(x0+ht,yy+ht/2)]))
   return bar
  if j=='ㄱ':shape=union(hb(0,w,hi),v(rr,0,h-ht*.4))
  elif j=='ㄴ':shape=union(v(x,0,h),hb(0,w,lo))
  elif j=='ㄷ':shape=union(v(x,0,h),hb(0,w,lo),hb(0,w,hi))
  elif j=='ㄹ':shape=union(hb(0,w,hi),v(rr,h*.5,h),hb(0,w,h*.5),v(x,0,h*.5),hb(0,w,lo))
  elif j in 'ㅁㅂ':
   shape=self.bowl(w,h,t,.018 if self.serif else .026)
   if j=='ㅂ':shape=union(shape,hb(t*.5,w-t*.5,h*.5))
   if self.serif:shape=union(shape,hb(0,w,hi),hb(0,w,lo))
  elif j=='ㅇ':shape=self.bowl(w,h,t)
  elif j in 'ㅅㅈㅊ':
   top=h-ht*.4 if j=='ㅅ' else h*.76 if j=='ㅈ' else h*.56;apex=w*.50
   left=curves([(x,t*.30),(w*.27,h*.32),(w*.44,top*.75),(apex,top)])
   right=curves([(apex,top),(w*.55,top*.73),(w*.74,h*.30),(rr,t*.30)])
   shape=union(self.stroke_path(left,t*.88),self.stroke_path(right,t*.88 if not self.serif else t*.66))
   if j!='ㅅ':shape=union(shape,hb(0,w,top))
   if j=='ㅊ':shape=union(shape,hb(w*.30,w*.70,h-ht*.4))
   if self.serif:shape=union(shape,Polygon([(apex-t*.42,top-t*.18),(apex-t*.55,top+t*.23),(apex+t*.44,top+t*.13),(apex+t*.34,top-t*.25)]))
  elif j=='ㅋ':shape=union(v(rr,0,h),hb(0,w,hi),hb(0,w,h*.49))
  elif j=='ㅌ':shape=union(v(x,0,h),hb(0,w,hi),hb(0,w,h*.5),hb(0,w,lo))
  elif j=='ㅍ':shape=union(hb(0,w,hi),hb(0,w,lo),v(w*.26,0,h),v(w*.74,0,h))
  elif j=='ㅎ':shape=union(affinity.translate(self.bowl(w*.85,h*.54,t*.87),xoff=w*.075),hb(0,w,h*.76),hb(w*.32,w*.68,h-ht*.40))
  else:raise ValueError(j)
  return self.round_join(shape,t)

 def vowel(self,j,w,h):
  t=limit_stroke(self.stroke*.90,min(w*.32,h*.30));ht=t*self.rules['horizontal_ratio']
  if j in 'ㅐㅒㅔㅖ':
   first={'ㅐ':'ㅏ','ㅒ':'ㅑ','ㅔ':'ㅓ','ㅖ':'ㅕ'}[j]
   return union(self.vowel(first,w*.62,h),affinity.translate(self.vowel('ㅣ',w*.20,h),xoff=w*.80))
  if j in VERTICAL:
   cx=t*.5 if j in 'ㅏㅑ' else w-t*.5 if j in 'ㅓㅕ' else w*.5
   shape=self.vbar(cx,0,h,t,False)
   if self.serif:shape=union(shape,Polygon([(cx-t*.5-7,h),(cx+t*.5,h+ht*.28),(cx+t*.5,h-ht*.4),(cx-t*.5,h-ht*.4)]))
   if j!='ㅣ':
    for yy in ([h*.43,h*.66] if j in 'ㅑㅕ' else [h*.53]):shape=union(shape,self.hbar(cx,w,yy,t) if j in 'ㅏㅑ' else self.hbar(0,cx,yy,t))
  elif j in HORIZONTAL:
   yy=ht*.5 if j in 'ㅗㅛ' else h-ht*.5 if j in 'ㅜㅠ' else h*.5;shape=self.hbar(0,w,yy,t)
   if j!='ㅡ':
    for xx in ([w*.36,w*.64] if j in 'ㅛㅠ' else [w*.5]):shape=union(shape,self.vbar(xx,yy,h,t,False) if j in 'ㅗㅛ' else self.vbar(xx,0,yy,t,False))
  else:raise ValueError(j)
  return self.round_join(shape,t)

 def combined_vowel(self,j,has_final):
  horiz,vert={'ㅘ':('ㅗ','ㅏ'),'ㅙ':('ㅗ','ㅐ'),'ㅚ':('ㅗ','ㅣ'),'ㅝ':('ㅜ','ㅓ'),'ㅞ':('ㅜ','ㅔ'),'ㅟ':('ㅜ','ㅣ'),'ㅢ':('ㅡ','ㅣ')}[j]
  yy=330 if has_final else 65
  return union(affinity.translate(self.vowel(horiz,550,130 if has_final else 225),xoff=70,yoff=yy),affinity.translate(self.vowel(vert,205,470 if has_final else 750),xoff=710,yoff=yy))

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


def component_shapes(design):
    """Natural contextual proportions; draw each component at its final size."""
    shapes={};layouts={}
    for form in ('v','h','m'):
        for final in (False,True):
            if form=='v':
                initial=(65,340,465,420) if final else (65,155,475,590)
                vowel=(650,300,265,530) if final else (650,20,265,810)
            elif form=='h':
                initial=(180,515,640,285) if final else (165,380,670,420)
                vowel=(65,300,870,130) if final else (65,20,870,235)
            else:
                initial=(70,505,505,295) if final else (70,380,505,420)
                vowel=None
            suffix=f'{form}{int(final)}'
            for index,j in enumerate(INITIALS):
                x,y,w,h=initial
                if form=='v' and j in 'ㄱㄲㄴㅋ':y-=25;h+=25
                if form=='h' and j in 'ㅇㅎ':
                    inset=90 if final else 25
                    x+=inset;w-=inset*2
                shapes[f'cho.{suffix}.{index}']=affinity.translate(design.consonant(j,w,h),xoff=x,yoff=y)
            for index,j in enumerate(VOWELS):
                actual='v' if j in VERTICAL else 'h' if j in HORIZONTAL else 'm'
                if actual!=form:continue
                if vowel:
                    x,y,w,h=vowel
                    shape=affinity.translate(design.vowel(j,w,h),xoff=x,yoff=y)
                else:shape=design.combined_vowel(j,final)
                shapes[f'jung.{suffix}.{index}']=shape
            layouts[(form,final)]=suffix
    for index,j in enumerate(FINALS[1:],1):
        w=680 if j in DOUBLE or j in CLUSTERS else 590
        h=230 if j in 'ㄱㄴㄷㄹㅋㅌ' else 220
        if j in 'ㅅㅈㅊ':w=495;h=225
        if j=='ㅇ':w=455;h=225
        if j=='ㅎ':w=480;h=245
        shapes[f'jong.{index}']=affinity.translate(design.consonant(j,w,h,design.stroke*.84),xoff=(1000-w)/2,yoff=-5)
    return shapes,layouts


def to_glyph(shape):
    pen=TTGlyphPen(None)
    if shape.is_empty:return pen.glyph()
    if not shape.is_valid:shape=shape.buffer(0)
    shape=shape.simplify(.20,preserve_topology=True)
    polygons=[shape] if shape.geom_type=='Polygon' else [g for g in shape.geoms if g.geom_type=='Polygon']
    for polygon in polygons:
        polygon=orient(polygon,sign=-1)
        for ring in [polygon.exterior,*polygon.interiors]:
            points=[]
            for x,y in list(ring.coords)[:-1]:
                pt=(round(x),round(y))
                if not points or pt!=points[-1]:points.append(pt)
            if len(points)<3:continue
            if points[-1]==points[0]:points.pop()
            pen.moveTo(points[0])
            for pt in points[1:]:pen.lineTo(pt)
            pen.closePath()
    return pen.glyph()
