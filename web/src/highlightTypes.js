import * as THREE from 'three';
import { lerp, rgbToHex } from './Util.js';
/**
 * Messy rewrite that was so long that it had to be in its own file.
 * DO NOT USE. This method has not been written to be used alone. You should use App::_highlightType which calls this method bound to the App internally.
 */
export default function highlightTypes(elements, type, highlight, outline, fade) {
  const vMode = this.state.visMode;
  if (fade.duration > 0) {
    let start;
    if (vMode === "2D") {
      start = {
        r: parseInt(elements[0].element.color.substr(1, 2), 16) / 255,
        g: parseInt(elements[0].element.color.substr(3, 2), 16) / 255,
        b: parseInt(elements[0].element.color.substr(5, 2), 16) / 255,
      };
    }
    else {
      let mat = (elements[0].element.__lineObj || elements[0].element.__threeObj).material;
      start = {
        r:mat.color.r,
        g:mat.color.g,
        b:mat.color.b
      };
    }
    let color;
    let opacity;
    if (highlight !== false) {
      color = new THREE.Color(highlight);
    }
    else {
      if (vMode !== "2D") {
        color = new THREE.Color(parseInt(elements[0].element.color.slice(1),16));
      }
      else {
        color = new THREE.Color(parseInt(elements[0].element.prevColor.slice(1),16));
      }
    }
    const end = {
      r:color.r,
      g:color.g,
      b:color.b
    };
    let id = type;
    if (typeof this._highlightTypeFadeIntervals[id] === "undefined") {
      this._highlightTypeFadeIntervals[id] = {};
    }
    if (typeof this._highlightTypeFadeIntervals[id].timeout !== "undefined") {
      // console.log('clear');
      clearInterval(this._highlightTypeFadeIntervals[id].timeout);
      clearInterval(this._highlightTypeFadeIntervals[id].interval);
      delete this._highlightTypeFadeIntervals[id].timeout;
      delete this._highlightTypeFadeIntervals[id].interval;
    }
    let timeoutPromise = new Promise((resolveTimeout) => {
      let theTimeout = setTimeout(() => {
        if (typeof this._highlightTypeFadeIntervals[id].interval !== "undefined") {
          // console.log('clear');
          clearInterval(this._highlightTypeFadeIntervals[id].interval);
          delete this._highlightTypeFadeIntervals[id].interval;
        }
        let intervalPromise = new Promise((resolveInterval) => {
          const duration = fade.duration;
          let interval = 15;
          let steps = duration / interval;
          let step_u = 1.0 / steps;
          let u = 0.0;
          // Slightly modified code from https://stackoverflow.com/a/11293378
          let theInterval = setInterval(() => {
            let r = lerp(start.r, end.r, u);
            let g = lerp(start.g, end.g, u);
            let b = lerp(start.b, end.b, u);
            for (let i=0;i<elements.length;i++) {
              let element = elements[i];
              let graphElementType = element.graphElementType;
              element = element.element;
              let obj = (element.__lineObj || element.__threeObj); //THREE.Mesh;
              let material;
              if (vMode !== "2D") {
                if (obj === undefined) continue;
                material = obj.material; // : THREE.MeshLambertMaterial
              }
              if (u >= 1.0) {
                if (vMode === "2D") {
                  element.color = rgbToHex(end.r*255,end.g*255,end.b*255);
                }
                else {
                  material.color = new THREE.Color(end.r,end.g,end.b);
                }
                // if (opacity !== undefined) material.opacity = opacity;
              }
              if (vMode === "2D") {
                element.color = rgbToHex(r*255,g*255,b*255);
              }
              else {
                material.color = new THREE.Color(r,g,b);
                // if (opacity !== undefined) material.opacity = opacity;
              }
            }
            u += step_u;
            if (u >= 1.0) {
              clearInterval(theInterval);
              resolveInterval();
            }
          }, interval);
          this._highlightTypeFadeIntervals[id].interval = theInterval;
        });
        resolveTimeout(intervalPromise);
      }, fade.offset);
      this._highlightTypeFadeIntervals[id].timeout = theTimeout;
    });
    return timeoutPromise;
  }
  else {
    for (let i=0;i<elements.length;i++) {
      let element = elements[i];
      let graphElementType = element.graphElementType;
      element = element.element;
      let obj = (element.__lineObj || element.__threeObj); //THREE.Mesh;
      let material;
      if (vMode !== "2D") {
        if (obj === undefined) return;
        material = obj.material; // : THREE.MeshLambertMaterial
      }
      let color;
      let opacity;
      if (highlight !== false) {
        color = new THREE.Color(highlight);
        if (vMode !== "2D") {
          element.prevOpacity = material.opacity;
        }
        else {
          element.prevColor = element.color;
        }
        opacity = 1;
      }
      else {
        if (vMode !== "2D") {
          color = new THREE.Color(parseInt(element.color.slice(1),16));
          opacity = element.prevOpacity;
          delete element.prevOpacity;
        }
        else {
          color = new THREE.Color(parseInt(element.prevColor.slice(1),16));
          // delete element.color;
        }
      }
      if (vMode === "2D") {
        element.color = "#" + color.getHexString();
      }
      else {
        material.color = color;
      }
    }
  }
}