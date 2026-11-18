function s(u){const o=u==null?void 0:u.audio_analysis,n=o==null?void 0:o.loudness_lufs;return typeof n!="number"||!Number.isFinite(n)||n>=-30?null:Math.round((-30-n)*10)/10}export{s as v};
