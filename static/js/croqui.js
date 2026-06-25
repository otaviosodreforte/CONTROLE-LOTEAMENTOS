var CroquiApp = {
    mode: 'quadra',
    currentPoints: [],
    quadras: [],
    selectedId: null,
    nextIds: { quadra: 1, lote: 1 },
    image: null,
    imageUrl: '',
    imageOrigW: 0,
    imageOrigH: 0,
    canvas: null,
    ctx: null,
    scale: 1,
    offsetX: 0,
    offsetY: 0,
    drawing: false,
    loteamentoId: null,
    mouseX: 0,
    mouseY: 0,

    init: function(loteamentoId, imageUrl) {
        this.loteamentoId = loteamentoId;
        this.imageUrl = imageUrl || '';
        this.canvas = document.getElementById('croquiCanvas');
        this.ctx = this.canvas.getContext('2d');
        var self = this;

        if (imageUrl) {
            this.loadImage(imageUrl);
        } else {
            this.resizeCanvas(800, 600);
            this.render();
        }

        this.canvas.addEventListener('click', function(e) { self.onClick(e); });
        this.canvas.addEventListener('dblclick', function(e) { self.onDblClick(e); });
        this.canvas.addEventListener('mousemove', function(e) { self.onMouseMove(e); });
        this.canvas.addEventListener('contextmenu', function(e) {
            e.preventDefault();
            self.onRightClick(e);
        });

        document.getElementById('btnModoQuadra').addEventListener('click', function() { self.setMode('quadra'); });
        document.getElementById('btnModoLote').addEventListener('click', function() { self.setMode('lote'); });
        document.getElementById('btnSalvar').addEventListener('click', function() { self.salvar(); });
        document.getElementById('btnLimpar').addEventListener('click', function() { self.limparTudo(); });
        document.getElementById('btnDeletar').addEventListener('click', function() { self.deletarSelecionado(); });
        document.getElementById('btnUpload').addEventListener('click', function() { self.uploadImage(); });

        this.setMode('quadra');
        this.setStatus('Pronto. Selecione um modo para começar.');
    },

    resizeCanvas: function(w, h) {
        var container = this.canvas.parentElement;
        var maxW = container.clientWidth - 4;
        var maxH = window.innerHeight - 80;
        if (maxW < 100) maxW = 800;
        if (maxH < 100) maxH = 600;
        var scaleX = maxW / (w || 800);
        var scaleY = maxH / (h || 600);
        this.scale = Math.min(scaleX, scaleY, 1);
        this.canvas.width = (w || 800) * this.scale;
        this.canvas.height = (h || 600) * this.scale;
        this.offsetX = 0;
        this.offsetY = 0;
    },

    loadImage: function(url) {
        var self = this;
        this.image = new Image();
        this.image.crossOrigin = 'anonymous';
        this.image.onload = function() {
            self.imageOrigW = self.image.naturalWidth || self.image.width;
            self.imageOrigH = self.image.naturalHeight || self.image.height;
            self.resizeCanvas(self.imageOrigW, self.imageOrigH);
            self.render();
            self.setStatus('Imagem carregada: ' + self.imageOrigW + 'x' + self.imageOrigH);
        };
        this.image.onerror = function() {
            self.setStatus('Erro ao carregar imagem.');
        };
        this.image.src = url;
    },

    setMode: function(mode) {
        this.mode = mode;
        document.getElementById('btnModoQuadra').className = 'btn' + (mode === 'quadra' ? ' ativo' : '');
        document.getElementById('btnModoLote').className = 'btn' + (mode === 'lote' ? ' ativo' : '');
        this.setStatus('Modo: ' + (mode === 'quadra' ? 'Desenhar Quadra' : 'Desenhar Lote') +
            ' — Clique para adicionar vértices, duplo-clique para fechar.');
    },

    setStatus: function(msg) {
        document.getElementById('statusBar').textContent = msg;
    },

    getCanvasCoords: function(e) {
        var rect = this.canvas.getBoundingClientRect();
        return {
            x: (e.clientX - rect.left) / this.scale,
            y: (e.clientY - rect.top) / this.scale
        };
    },

    onClick: function(e) {
        var p = this.getCanvasCoords(e);
        this.currentPoints.push({ x: p.x, y: p.y });
        this.render();
    },

    onDblClick: function(e) {
        if (this.currentPoints.length < 3) {
            this.currentPoints = [];
            this.render();
            return;
        }
        this.fecharPoligono();
    },

    onRightClick: function(e) {
        if (this.currentPoints.length > 0) {
            this.currentPoints.pop();
            this.render();
        }
    },

    onMouseMove: function(e) {
        var p = this.getCanvasCoords(e);
        this.mouseX = p.x;
        this.mouseY = p.y;
        if (this.currentPoints.length > 0) {
            this.render();
        }
    },

    fecharPoligono: function() {
        var self = this;
        var pts = this.currentPoints.slice();
        this.currentPoints = [];

        if (this.mode === 'quadra') {
            var nome = prompt('Nome da Quadra (ex: A, B, C):', 'Quadra ' + this.nextIds.quadra);
            if (!nome) { this.render(); return; }
            this.quadras.push({
                id: 'q' + (this.nextIds.quadra++),
                label: nome,
                polygon: pts,
                lotes: [],
                color: '#1565c0'
            });
            this.selectedId = null;
            this.render();
            this.atualizarLista();
        } else {
            if (this.quadras.length === 0) {
                alert('Desenhe as quadras primeiro antes de adicionar lotes.');
                this.currentPoints = pts;
                this.render();
                return;
            }
            var quadraOpts = this.quadras.map(function(q, i) {
                return (i + 1) + ' - ' + q.label;
            }).join('\n');
            var idx = prompt('Escolha a Quadra (digite o número):\n' + quadraOpts, '1');
            if (!idx) { this.render(); return; }
            var qi = parseInt(idx) - 1;
            if (isNaN(qi) || qi < 0 || qi >= this.quadras.length) {
                alert('Quadra inválida!');
                this.render();
                return;
            }
            var num = prompt('Número do Lote:', '' + this.quadras[qi].lotes.length + 1);
            if (!num) { this.render(); return; }
            this.quadras[qi].lotes.push({
                id: 'l' + (this.nextIds.lote++),
                label: num,
                polygon: pts,
                color: '#4caf50'
            });
            this.selectedId = null;
            this.render();
            this.atualizarLista();
        }
    },

    selecionar: function(id) {
        this.selectedId = (this.selectedId === id) ? null : id;
        this.render();
        this.atualizarLista();
    },

    deletarSelecionado: function() {
        if (!this.selectedId) return;
        if (!confirm('Deletar polígono selecionado?')) return;
        for (var i = 0; i < this.quadras.length; i++) {
            var q = this.quadras[i];
            if (q.id === this.selectedId) {
                this.quadras.splice(i, 1);
                this.selectedId = null;
                this.render();
                this.atualizarLista();
                this.setStatus('Quadra deletada.');
                return;
            }
            for (var j = 0; j < q.lotes.length; j++) {
                if (q.lotes[j].id === this.selectedId) {
                    q.lotes.splice(j, 1);
                    this.selectedId = null;
                    this.render();
                    this.atualizarLista();
                    this.setStatus('Lote deletado.');
                    return;
                }
            }
        }
        this.selectedId = null;
    },

    limparTudo: function() {
        if (!confirm('Limpar todos os polígonos?')) return;
        this.quadras = [];
        this.currentPoints = [];
        this.selectedId = null;
        this.nextIds = { quadra: 1, lote: 1 };
        this.render();
        this.atualizarLista();
        this.setStatus('Tudo limpo.');
    },

    polygonCentroid: function(pts) {
        var cx = 0, cy = 0;
        for (var i = 0; i < pts.length; i++) {
            cx += pts[i].x;
            cy += pts[i].y;
        }
        return { x: cx / pts.length, y: cy / pts.length };
    },

    pointInPolygon: function(px, py, pts) {
        var inside = false;
        for (var i = 0, j = pts.length - 1; i < pts.length; j = i++) {
            var xi = pts[i].x, yi = pts[i].y;
            var xj = pts[j].x, yj = pts[j].y;
            if ((yi > py) !== (yj > py) && px < (xj - xi) * (py - yi) / (yj - yi) + xi) {
                inside = !inside;
            }
        }
        return inside;
    },

    hitTest: function(px, py) {
        for (var i = this.quadras.length - 1; i >= 0; i--) {
            var q = this.quadras[i];
            for (var j = q.lotes.length - 1; j >= 0; j--) {
                var l = q.lotes[j];
                if (this.pointInPolygon(px, py, l.polygon)) return l.id;
            }
            if (this.pointInPolygon(px, py, q.polygon)) return q.id;
        }
        return null;
    },

    render: function() {
        var ctx = this.ctx;
        var w = this.canvas.width;
        var h = this.canvas.height;
        ctx.clearRect(0, 0, w, h);

        ctx.save();
        ctx.scale(this.scale, this.scale);

        if (this.image && this.image.complete && this.image.naturalWidth > 0) {
            ctx.drawImage(this.image, 0, 0, this.imageOrigW, this.imageOrigH);
        } else {
            ctx.fillStyle = '#f5f5f5';
            ctx.fillRect(0, 0, this.imageOrigW || 800, this.imageOrigH || 600);
            ctx.fillStyle = '#999';
            ctx.font = '18px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('Carregue uma imagem do croqui', (this.imageOrigW || 800) / 2, (this.imageOrigH || 600) / 2);
        }

        var self = this;

        function drawPolygon(pts, fillColor, strokeColor, lineWidth, dash, label, isSelected) {
            if (!pts || pts.length < 3) return;
            ctx.beginPath();
            ctx.moveTo(pts[0].x, pts[0].y);
            for (var i = 1; i < pts.length; i++) {
                ctx.lineTo(pts[i].x, pts[i].y);
            }
            ctx.closePath();
            ctx.fillStyle = fillColor || 'rgba(0,0,0,0)';
            ctx.fill();
            ctx.strokeStyle = strokeColor || '#333';
            ctx.lineWidth = lineWidth || 2;
            if (dash) ctx.setLineDash(dash);
            ctx.stroke();
            ctx.setLineDash([]);
            if (isSelected) {
                ctx.strokeStyle = '#ff5722';
                ctx.lineWidth = 3;
                ctx.setLineDash([5, 5]);
                ctx.stroke();
                ctx.setLineDash([]);
                for (var k = 0; k < pts.length; k++) {
                    ctx.beginPath();
                    ctx.arc(pts[k].x, pts[k].y, 4, 0, Math.PI * 2);
                    ctx.fillStyle = '#ff5722';
                    ctx.fill();
                    ctx.strokeStyle = '#fff';
                    ctx.lineWidth = 1;
                    ctx.stroke();
                }
            }
            if (label) {
                var c = self.polygonCentroid(pts);
                ctx.fillStyle = '#fff';
                ctx.font = 'bold 13px sans-serif';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.shadowColor = 'rgba(0,0,0,0.7)';
                ctx.shadowBlur = 3;
                ctx.fillText(label, c.x, c.y);
                ctx.shadowBlur = 0;
            }
        }

        for (var i = 0; i < this.quadras.length; i++) {
            var q = this.quadras[i];
            var sel = q.id === this.selectedId;
            drawPolygon(q.polygon, 'rgba(21,101,192,0.12)', '#1565c0', 2, null, q.label, sel);
            ctx.fillStyle = '#1565c0';
            ctx.font = 'bold 14px sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';
            var c = this.polygonCentroid(q.polygon);
            ctx.shadowColor = 'rgba(0,0,0,0.7)';
            ctx.shadowBlur = 3;
            ctx.fillStyle = '#fff';
            ctx.fillText('(' + q.label + ') ' + q.lotes.length + ' lotes', c.x, c.y + 8);
            ctx.shadowBlur = 0;

            for (var j = 0; j < q.lotes.length; j++) {
                var l = q.lotes[j];
                var lsel = l.id === this.selectedId;
                drawPolygon(l.polygon, 'rgba(76,175,80,0.15)', '#4caf50', 1.5, null, l.label, lsel);
            }
        }

        if (this.currentPoints.length > 0) {
            ctx.beginPath();
            ctx.moveTo(this.currentPoints[0].x, this.currentPoints[0].y);
            for (var i = 1; i < this.currentPoints.length; i++) {
                ctx.lineTo(this.currentPoints[i].x, this.currentPoints[i].y);
            }
            ctx.lineTo(this.mouseX, this.mouseY);
            ctx.strokeStyle = '#f44336';
            ctx.lineWidth = 2;
            ctx.setLineDash([6, 4]);
            ctx.stroke();
            ctx.setLineDash([]);
            for (var i = 0; i < this.currentPoints.length; i++) {
                ctx.beginPath();
                ctx.arc(this.currentPoints[i].x, this.currentPoints[i].y, 4, 0, Math.PI * 2);
                ctx.fillStyle = '#f44336';
                ctx.fill();
                ctx.strokeStyle = '#fff';
                ctx.lineWidth = 1;
                ctx.stroke();
            }
        }

        ctx.restore();
    },

    atualizarLista: function() {
        var list = document.getElementById('poligonoList');
        list.innerHTML = '';
        var self = this;

        if (this.quadras.length === 0) {
            list.innerHTML = '<div class="vazio">Nenhum polígono desenhado.</div>';
            return;
        }

        for (var i = 0; i < this.quadras.length; i++) {
            var q = this.quadras[i];
            var div = document.createElement('div');
            div.className = 'item-poly' + (q.id === this.selectedId ? ' selecionado' : '');
            div.innerHTML = '<span class="quadra-label" data-id="' + q.id + '">🟦 ' +
                q.label + ' (' + q.lotes.length + ' lotes)</span>';
            div.addEventListener('click', function(id) {
                return function() { self.selecionar(id); };
            }(q.id));
            list.appendChild(div);

            for (var j = 0; j < q.lotes.length; j++) {
                var l = q.lotes[j];
                var ldiv = document.createElement('div');
                ldiv.className = 'item-poly lote' + (l.id === this.selectedId ? ' selecionado' : '');
                ldiv.innerHTML = '<span class="lote-label" data-id="' + l.id + '">🟩 Lote ' + l.label + '</span>';
                ldiv.addEventListener('click', function(id) {
                    return function() { self.selecionar(id); };
                }(l.id));
                list.appendChild(ldiv);
            }
        }
    },

    salvar: function() {
        var quadrasData = [];
        for (var i = 0; i < this.quadras.length; i++) {
            var q = this.quadras[i];
            var lotesData = [];
            for (var j = 0; j < q.lotes.length; j++) {
                lotesData.push({
                    label: q.lotes[j].label,
                    polygon: q.lotes[j].polygon
                });
            }
            quadrasData.push({
                label: q.label,
                polygon: q.polygon,
                lotes: lotesData
            });
        }

        var payload = {
            image: this.imageUrl.split('/').pop(),
            image_width: this.imageOrigW,
            image_height: this.imageOrigH,
            quadras: quadrasData
        };

        var self = this;
        this.setStatus('Salvando...');
        fetch('/api/croqui/salvar/' + this.loteamentoId, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        }).then(function(r) {
            if (!r.ok) { return r.text().then(function(t) { throw new Error(t.substring(0,200)); }); }
            return r.json();
        }).then(function(data) {
            if (data.ok) {
                self.setStatus('✅ Dados salvos com sucesso!');
            } else {
                self.setStatus('❌ Erro ao salvar: ' + (data.erro || 'desconhecido'));
            }
        }).catch(function(err) {
            self.setStatus('❌ Erro: ' + err.message);
        });
    },

    uploadImage: function() {
        var input = document.getElementById('fileInput');
        if (!input.files || !input.files[0]) {
            alert('Selecione uma imagem primeiro.');
            return;
        }
        var file = input.files[0];
        var formData = new FormData();
        formData.append('file', file);

        var self = this;
        this.setStatus('Enviando imagem...');
        fetch('/api/croqui/upload/' + this.loteamentoId, {
            method: 'POST',
            body: formData
        }).then(function(r) {
            if (!r.ok) { return r.text().then(function(t) { throw new Error(t.substring(0,200)); }); }
            return r.json();
        }).then(function(data) {
            if (data.ok) {
                self.setStatus('Imagem enviada!');
                self.loadImage('/static/croquis/' + data.image);
                document.getElementById('uploadArea').style.display = 'none';
                document.getElementById('canvasArea').style.display = 'block';
            } else {
                self.setStatus('❌ Erro no upload: ' + (data.erro || 'desconhecido'));
            }
        }).catch(function(err) {
            self.setStatus('❌ Erro: ' + err.message);
        });
    },

    carregarDados: function() {
        var self = this;
        fetch('/api/croqui/dados/' + this.loteamentoId)
            .then(function(r) {
                if (!r.ok) { return r.text().then(function(t) { throw new Error(t.substring(0,200)); }); }
                return r.json();
            }).then(function(data) {
                if (data.erro) {
                    self.setStatus('Aviso: ' + data.erro);
                    return;
                }
                if (data.image) {
                    if (self.imageUrl !== '/static/croquis/' + data.image) {
                        self.imageUrl = '/static/croquis/' + data.image;
                        self.loadImage(self.imageUrl);
                    }
                    document.getElementById('uploadArea').style.display = 'none';
                    document.getElementById('canvasArea').style.display = 'block';
                }
                if (data.quadras && data.quadras.length > 0) {
                    self.quadras = [];
                    for (var i = 0; i < data.quadras.length; i++) {
                        var q = data.quadras[i];
                        var qObj = {
                            id: 'q' + (self.nextIds.quadra++),
                            label: q.label,
                            polygon: q.polygon,
                            lotes: [],
                            color: '#1565c0'
                        };
                        if (q.lotes) {
                            for (var j = 0; j < q.lotes.length; j++) {
                                qObj.lotes.push({
                                    id: 'l' + (self.nextIds.lote++),
                                    label: q.lotes[j].label,
                                    polygon: q.lotes[j].polygon,
                                    color: '#4caf50'
                                });
                            }
                        }
                        self.quadras.push(qObj);
                    }
                    self.render();
                    self.atualizarLista();
                    self.setStatus('Dados carregados: ' + self.quadras.length + ' quadras.');
                }
            })
            .catch(function(err) {
                self.setStatus('Erro ao carregar dados: ' + err.message);
            });
    }
};
